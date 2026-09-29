import csv
import json
import os
import uuid
from typing import List, Dict, Any, Generator
import pandas as pd
from src.models.incident import RawIncident, EnrichedIncident
from src.agents.classifier_agent import MultiTechClassifierAgent
from src.agents.taxonomy_agent import DynamicTaxonomyAgent

class IncidentBatchProcessor:
    def __init__(
        self,
        classifier: MultiTechClassifierAgent,
        taxonomy_agent: DynamicTaxonomyAgent,
        output_enriched_path: str = "data/enriched_incidents.json"
    ):
        self.classifier = classifier
        self.taxonomy_agent = taxonomy_agent
        self.output_enriched_path = output_enriched_path

    @staticmethod
    def _find_field(row: Dict[str, Any], candidates: List[str], default: str = "") -> str:
        for c in candidates:
            for k in row.keys():
                if k and str(k).strip().lower() == c.lower():
                    val = row.get(k)
                    if pd.notna(val) and val is not None:
                        return str(val).strip()
        return default

    @classmethod
    def parse_incident_row(cls, row: Dict[str, Any], fallback_id: str) -> RawIncident:
        inc_id = cls._find_field(row, ["incident_id", "id", "ticket_id", "number", "key", "issue_key"], fallback_id)
        title = cls._find_field(row, ["title", "summary", "short_description", "headline", "subject", "name"], "Untitled Incident")
        description = cls._find_field(row, ["description", "details", "body", "issue_description", "logs", "content"], title)
        resolution = cls._find_field(row, ["resolution", "resolution_notes", "root_cause_notes", "fix", "solution", "close_notes"], "")
        severity = cls._find_field(row, ["severity", "priority", "urgency", "impact"], "SEV-3")

        return RawIncident(
            incident_id=inc_id,
            title=title,
            description=description,
            resolution=resolution,
            severity=severity
        )

    def iter_file_records(self, filepath: str, chunksize: int = 1000) -> Generator[List[RawIncident], None, None]:
        """
        Memory-efficient streaming generator supporting files of any volume (thousands/millions of rows).
        Supports:
          - CSV (.csv) via chunked read
          - Excel (.xlsx, .xls) via openpyxl streaming generator
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        ext = os.path.splitext(filepath)[1].lower()

        if ext in [".xlsx", ".xls"]:
            # Stream excel file rows without loading entire workbook into RAM
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
            sheet = wb.active
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
            # CSV stream with pandas chunksize
            row_idx = 1
            for df_chunk in pd.read_csv(filepath, chunksize=chunksize, dtype=str, on_bad_lines='skip'):
                records = df_chunk.to_dict(orient="records")
                chunk = []
                for r in records:
                    inc = self.parse_incident_row(r, fallback_id=f"INC-{row_idx:05d}")
                    chunk.append(inc)
                    row_idx += 1
                yield chunk

    def process_file_stream(self, filepath: str, max_incidents: int = 50000, progress_cb=None) -> List[EnrichedIncident]:
        """Processes an Excel or CSV file of arbitrary volume."""
        all_enriched: List[EnrichedIncident] = []
        total_seen = 0

        for chunk in self.iter_file_records(filepath):
            for inc in chunk:
                sig = self.classifier.classify_incident(inc)
                path = self.taxonomy_agent.evolve_taxonomy(inc, sig)
                enriched = EnrichedIncident(
                    incident=inc,
                    classification=sig,
                    taxonomy_path=path
                )
                all_enriched.append(enriched)
                total_seen += 1
                if progress_cb and total_seen % 50 == 0:
                    progress_cb(total_seen)

                if total_seen >= max_incidents:
                    break
            if total_seen >= max_incidents:
                break

        # Persist updated taxonomy and records
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
        return self.process_file_stream(csv_filepath)
