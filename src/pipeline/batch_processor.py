import json
import os
import re
import uuid
from typing import List, Dict, Any, Generator, Optional
import pandas as pd
from src.models.incident import RawIncident, EnrichedIncident
from src.agents.classifier_agent import MultiTechClassifierAgent
from src.agents.taxonomy_agent import DynamicTaxonomyAgent


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
    ):
        self.classifier = classifier
        self.taxonomy_agent = taxonomy_agent
        self.output_enriched_path = output_enriched_path
        self.classification_hint = classification_hint

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
            raise FileNotFoundError(f"File not found: {filepath}")

        ext = os.path.splitext(filepath)[1].lower()

        if ext in [".xlsx", ".xls"]:
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
            # Pick the sheet with the most non-empty header columns (avoids pivot/summary sheets)
            best_sheet = wb.active
            best_col_count = 0
            for ws in wb.worksheets:
                rows = ws.iter_rows(values_only=True)
                try:
                    hdr = next(rows)
                    col_count = sum(1 for h in hdr if h is not None)
                    if col_count > best_col_count:
                        best_col_count = col_count
                        best_sheet = ws
                except StopIteration:
                    pass
            sheet = best_sheet
            rows_iter = sheet.iter_rows(values_only=True)
            try:
                header_row = next(rows_iter)
            except StopIteration:
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

    def process_file_stream(
        self,
        filepath: str,
        max_incidents: int = 50000,
        progress_cb=None,
    ) -> List[EnrichedIncident]:
        all_enriched: List[EnrichedIncident] = []
        total_seen = 0

        for chunk in self.iter_file_records(filepath):
            for inc in chunk:
                sig = self.classifier.classify_incident(
                    inc, classification_hint=self.classification_hint
                )
                path = self.taxonomy_agent.evolve_taxonomy(inc, sig)
                enriched = EnrichedIncident(
                    incident=inc,
                    classification=sig,
                    taxonomy_path=path,
                )
                all_enriched.append(enriched)
                total_seen += 1
                if progress_cb and total_seen % 50 == 0:
                    progress_cb(total_seen)

                if total_seen >= max_incidents:
                    break
            if total_seen >= max_incidents:
                break

        self.taxonomy_agent.save()

        # Merge with existing enriched file if present
        existing = []
        if os.path.exists(self.output_enriched_path):
            try:
                with open(self.output_enriched_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        combined = existing + [item.model_dump() for item in all_enriched]
        os.makedirs(os.path.dirname(self.output_enriched_path), exist_ok=True)
        with open(self.output_enriched_path, "w", encoding="utf-8") as f:
            json.dump(combined, f, indent=2)

        return all_enriched

    def process_csv(self, csv_filepath: str) -> List[EnrichedIncident]:
        # Kept for backward compatibility; delegates to process_file_stream.
        return self.process_file_stream(csv_filepath)
