import json
import math
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Generator, Optional
import pandas as pd
from src.agents.base_llm import LLMUnavailableError
from src.models.incident import RawIncident, EnrichedIncident
from src.agents.classifier_agent import MultiTechClassifierAgent
from src.agents.taxonomy_agent import DynamicTaxonomyAgent
from src.logging_config import get_logger, log_timing

logger = get_logger("pipeline.batch")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except ValueError:
        return default


def _select_excel_sheet(wb):
    """Pick the sheet with the most non-empty header columns (avoids pivot/summary sheets)."""
    best_sheet = wb.active
    best_col_count = 0
    for ws in wb.worksheets:
        try:
            hdr = next(ws.iter_rows(values_only=True))
        except StopIteration:
            continue
        col_count = sum(1 for h in hdr if h is not None)
        if col_count > best_col_count:
            best_col_count = col_count
            best_sheet = ws
    return best_sheet, best_col_count


def _norm(s: str) -> str:
    """Normalize a column header: lowercase, strip spaces/underscores/hyphens."""
    return re.sub(r'[\s_\-]+', '', s).lower()


class IncidentBatchProcessor:
    def __init__(
        self,
        classifier: MultiTechClassifierAgent,
        taxonomy_agent: DynamicTaxonomyAgent,
        output_enriched_path: str = "data/enriched_incidents.json",
        classification_hint: str = "",
        require_llm: bool = True,
    ):
        self.classifier = classifier
        self.taxonomy_agent = taxonomy_agent
        self.output_enriched_path = output_enriched_path
        self.classification_hint = classification_hint
        # When True, processing stops if the LLM is unavailable instead of
        # falling back to the heuristic extractor.
        self.require_llm = require_llm
        # Stats of the most recent run (total_rows, batch_size, workers, processed, failed, ...)
        self.last_run: Dict[str, Any] = {}

    @staticmethod
    def _find_field(row: Dict[str, Any], candidates: List[str], default: str = "") -> str:
        # Build a normalized-key → (original_key, value) lookup once per row
        norm_map: Dict[str, tuple] = {}
        for k in row.keys():
            if k:
                norm_map[_norm(str(k))] = (k, row[k])

        for c in candidates:
            entry = norm_map.get(_norm(c))
            if entry:
                val = entry[1]
                if pd.notna(val) and val is not None:
                    return str(val).strip()
        return default

    @classmethod
    def parse_incident_row(cls, row: Dict[str, Any], fallback_id: str) -> RawIncident:
        inc_id = cls._find_field(row, ["incident_id", "id", "ticket_id", "number", "key", "issue_key", "incident_sheet"], fallback_id)
        # "description" added last so SAP files (where Description = short summary) still map here as a fallback
        title = cls._find_field(row, ["title", "summary", "short_description", "headline", "subject", "name", "description"], "Untitled Incident")
        # Prefer extended/full description over the short one; fall back to title text
        description = cls._find_field(row, ["extended_description", "extended_desc", "extended desc", "details", "body", "issue_description", "logs", "content", "description"], title)
        # SAP: Latest Comment or Solution Category carry resolution context
        resolution = cls._find_field(row, ["resolution", "resolution_notes", "root_cause_notes", "fix", "solution", "close_notes", "latest_comment", "solution_category"], "")
        severity = cls._find_field(row, ["severity", "priority", "urgency", "impact"], "SEV-3")

        # Append SAP-specific metadata to description so the classifier can use it.
        # Only use the actual SAP Component code field — never the numeric Configuration Item CI ID.
        sap_comp = cls._find_field(row, ["sap_component", "sap component"], "")
        system_id = cls._find_field(row, ["system_id", "system id", "systemid"], "")
        support_team = cls._find_field(row, ["support_team", "support team", "supportteam", "service_team", "service team"], "")
        if sap_comp or system_id or support_team:
            meta_parts = []
            if sap_comp:
                meta_parts.append(f"SAP Component: {sap_comp}")
            if system_id:
                meta_parts.append(f"System ID: {system_id}")
            if support_team:
                meta_parts.append(f"Support Team: {support_team}")
            description = description + " [" + "; ".join(meta_parts) + "]"

        return RawIncident(
            incident_id=inc_id,
            title=title,
            description=description,
            resolution=resolution,
            severity=severity,
        )

    def iter_file_records(self, filepath: str, chunksize: int = 1000) -> Generator[List[RawIncident], None, None]:
        """
        Memory-efficient streaming generator supporting files of any volume.
        Supports CSV (.csv) and Excel (.xlsx, .xls).
        """
        if not os.path.exists(filepath):
            logger.error("Input file not found: %s", filepath)
            raise FileNotFoundError(f"File not found: {filepath}")

        ext = os.path.splitext(filepath)[1].lower()
        logger.info("Parsing file=%s format=%s chunksize=%d", os.path.basename(filepath), ext or "csv", chunksize)

        if ext in [".xlsx", ".xls"]:
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
            sheet, best_col_count = _select_excel_sheet(wb)
            logger.info("Excel sheet selected: %s (%d header columns)", getattr(sheet, "title", "?"), best_col_count)
            rows_iter = sheet.iter_rows(values_only=True)
            try:
                header_row = next(rows_iter)
            except StopIteration:
                logger.warning("Excel sheet has no rows: %s", filepath)
                return

            headers = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(header_row)]
            chunk = []
            row_idx = 1
            for row in rows_iter:
                if not any(row):
                    continue
                row_dict = {headers[i]: (row[i] if i < len(row) else None) for i in range(len(headers))}
                inc = self.parse_incident_row(row_dict, fallback_id=f"EXCEL-{row_idx:05d}")
                chunk.append(inc)
                row_idx += 1
                if len(chunk) >= chunksize:
                    yield chunk
                    chunk = []
            if chunk:
                yield chunk
            wb.close()

        else:
            row_idx = 1
            for df_chunk in pd.read_csv(filepath, chunksize=chunksize, dtype=str, on_bad_lines='skip'):
                records = df_chunk.to_dict(orient="records")
                chunk = []
                for r in records:
                    inc = self.parse_incident_row(r, fallback_id=f"INC-{row_idx:05d}")
                    chunk.append(inc)
                    row_idx += 1
                yield chunk

    def count_rows(self, filepath: str) -> int:
        """Count data rows (excluding the header) without building incident objects."""
        ext = os.path.splitext(filepath)[1].lower()
        if ext in [".xlsx", ".xls"]:
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
            try:
                sheet, _ = _select_excel_sheet(wb)
                return sum(1 for row in sheet.iter_rows(min_row=2, values_only=True) if any(row))
            finally:
                wb.close()
        total = 0
        for df_chunk in pd.read_csv(filepath, chunksize=5000, dtype=str, on_bad_lines='skip', usecols=[0]):
            total += len(df_chunk)
        return total

    @staticmethod
    def plan_batches(total_rows: int) -> tuple:
        """Return (batch_size, workers) sized from the row count; env vars override."""
        workers = min(_env_int("LLM_MAX_WORKERS", 5), max(1, total_rows))
        env_batch = os.getenv("BATCH_SIZE")
        if env_batch and env_batch.isdigit() and int(env_batch) > 0:
            batch_size = int(env_batch)
        else:
            # Aim for ~2 batches per worker, kept within 10..50 rows per batch
            batch_size = min(50, max(10, math.ceil(total_rows / (workers * 2))))
        return batch_size, workers

    def _classify_batch(self, executor, batch: List[RawIncident]):
        """Classify a batch in parallel; returns [(incident, signature|None, error|None)] in order."""
        def work(inc):
            try:
                return inc, self.classifier.classify_incident(
                    inc, classification_hint=self.classification_hint, strict=self.require_llm
                ), None
            except Exception as e:
                return inc, None, e
        return list(executor.map(work, batch))

    def process_file_stream(
        self,
        filepath: str,
        max_incidents: int = 50000,
        progress_cb=None,
    ) -> List[EnrichedIncident]:
        """
        Classify incidents in small parallel batches. With require_llm, a failed
        LLM preflight raises LLMUnavailableError before any work is done, and
        repeated failing batches abort the run (completed batches are still saved).

        progress_cb(processed, total_rows) is called after every batch.
        """
        all_enriched: List[EnrichedIncident] = []
        batch_start = time.perf_counter()
        logger.info(
            "Batch start file=%s max_incidents=%d hint=%s output=%s require_llm=%s",
            os.path.basename(filepath), max_incidents, bool(self.classification_hint),
            self.output_enriched_path, self.require_llm,
        )

        if self.require_llm:
            with log_timing(logger, "LLM preflight"):
                self.classifier.llm.check_available()

        with log_timing(logger, "row count", file=os.path.basename(filepath)):
            total_rows = self.count_rows(filepath)
        total = min(total_rows, max_incidents)
        batch_size, workers = self.plan_batches(total)
        max_failed_batches = _env_int("LLM_MAX_FAILED_BATCHES", 3)
        self.last_run = {
            "total_rows": total_rows, "to_process": total, "batch_size": batch_size,
            "workers": workers, "processed": 0, "failed": 0, "batches": 0, "aborted": False,
        }
        logger.info("Plan: rows=%d to_process=%d batch_size=%d workers=%d", total_rows, total, batch_size, workers)

        failures = 0
        failed_ids: List[str] = []
        consecutive_failed = 0
        t_parse = t_classify = t_taxonomy = 0.0
        chunk_iter = iter(self.iter_file_records(filepath, chunksize=batch_size))
        abort_error: Optional[LLMUnavailableError] = None

        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="classify") as executor:
                while len(all_enriched) + failures < total:
                    t0 = time.perf_counter()
                    try:
                        batch = next(chunk_iter)
                    except StopIteration:
                        break
                    except Exception:
                        logger.exception("Parsing failed file=%s after %d incidents", filepath, len(all_enriched))
                        raise
                    t_parse += time.perf_counter() - t0
                    batch = batch[: total - len(all_enriched) - failures]
                    self.last_run["batches"] += 1

                    t0 = time.perf_counter()
                    results = self._classify_batch(executor, batch)
                    t_classify += time.perf_counter() - t0

                    llm_errors = [e for _, _, e in results if isinstance(e, LLMUnavailableError)]
                    other_errors = [(i, e) for i, _, e in results
                                    if e is not None and not isinstance(e, LLMUnavailableError)]
                    for inc, e in other_errors:
                        logger.error("Incident processing failed id=%s: %s", inc.incident_id, e, exc_info=e)

                    # Taxonomy evolution mutates shared state, so it stays sequential
                    t0 = time.perf_counter()
                    for inc, sig, err in results:
                        if err is not None:
                            failures += 1
                            failed_ids.append(inc.incident_id)
                            continue
                        path = self.taxonomy_agent.evolve_taxonomy(inc, sig)
                        all_enriched.append(EnrichedIncident(incident=inc, classification=sig, taxonomy_path=path))
                    t_taxonomy += time.perf_counter() - t0

                    done = len(all_enriched)
                    n_failed = len(llm_errors) + len(other_errors)
                    self.last_run.update(processed=done, failed=failures)
                    if progress_cb:
                        progress_cb(done, total)
                    logger.info("Batch %d done: ok=%d failed=%d progress=%d/%d elapsed=%.1fs",
                                self.last_run["batches"], len(batch) - n_failed, n_failed,
                                done, total, time.perf_counter() - batch_start)

                    if llm_errors and len(llm_errors) == len(batch):
                        consecutive_failed += 1
                        logger.warning("All LLM calls failed in batch %d (%d/%d consecutive)",
                                       self.last_run["batches"], consecutive_failed, max_failed_batches)
                        if consecutive_failed >= max_failed_batches:
                            abort_error = LLMUnavailableError(
                                f"LLM unavailable: {consecutive_failed} consecutive batches failed "
                                f"({llm_errors[0]})"
                            )
                            break
                    else:
                        consecutive_failed = 0
        finally:
            logger.info(
                "Stage timings parse=%.2fs classify=%.2fs taxonomy=%.2fs (incidents=%d batches=%d)",
                t_parse, t_classify, t_taxonomy, len(all_enriched), self.last_run["batches"],
            )
            self._persist(all_enriched, batch_start, failures)

        if failed_ids:
            logger.warning("Skipped %d incidents that failed classification: %s%s", len(failed_ids),
                           failed_ids[:20], " ..." if len(failed_ids) > 20 else "")
        if abort_error:
            self.last_run["aborted"] = True
            remaining = total - len(all_enriched) - failures
            logger.error("ABORTED: %s. processed=%d failed=%d remaining=%d (partial results saved)",
                         abort_error, len(all_enriched), failures, remaining)
            abort_error.stats = {**self.last_run, "remaining": remaining}
            raise abort_error
        return all_enriched

    def _persist(self, all_enriched: List[EnrichedIncident], batch_start: float, failures: int) -> None:
        with log_timing(logger, "taxonomy save"):
            self.taxonomy_agent.save()

        # Merge with existing enriched file if present
        with log_timing(logger, "persist enriched incidents", path=self.output_enriched_path, new=len(all_enriched)):
            existing = []
            if os.path.exists(self.output_enriched_path):
                try:
                    with open(self.output_enriched_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    logger.warning(
                        "Existing enriched file unreadable, starting fresh: %s",
                        self.output_enriched_path, exc_info=True,
                    )
                    existing = []

            combined = existing + [item.model_dump() for item in all_enriched]
            os.makedirs(os.path.dirname(self.output_enriched_path), exist_ok=True)
            with open(self.output_enriched_path, "w", encoding="utf-8") as f:
                json.dump(combined, f, indent=2)

        logger.info(
            "Batch complete processed=%d failed=%d existing=%d total_persisted=%d in %.2fs",
            len(all_enriched), failures, len(existing), len(combined), time.perf_counter() - batch_start,
        )

    def process_csv(self, csv_filepath: str) -> List[EnrichedIncident]:
        # Kept for backward compatibility; delegates to process_file_stream.
        return self.process_file_stream(csv_filepath)
