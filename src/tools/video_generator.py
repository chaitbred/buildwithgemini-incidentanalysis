"""Tool for generating diagnostic and incident simulation videos using Google Omni model.

Uses Google's Omni model (gemini-omni-flash-preview) in the global region.
Saves video artifacts to tool_context.save_artifact so they appear in ADK Playground,
and uploads raw bytes to Google Cloud Storage public bucket, returning the public HTTPS URL.
"""

import os
import uuid
import base64
import logging
from typing import Optional, Dict, Any

from google import genai
from google.genai import types as genai_types
from google.cloud import storage
from google.adk.tools import ToolContext

logger = logging.getLogger("video_generator")

GCS_BUCKET_NAME = "bwg3-qwiklabs-gcp-04-7df709bbadfe"
PROJECT_ID = "qwiklabs-gcp-04-7df709bbadfe"
OMNI_MODEL_NAME = "gemini-omni-flash-preview"


def _extract_video_bytes(interaction: Any) -> Optional[bytes]:
    """Robustly extracts video bytes from interaction object or its dictionary representation."""
    def _to_bytes(val: Any) -> Optional[bytes]:
        if val is None:
            return None
        if isinstance(val, bytes):
            return val
        if isinstance(val, str):
            try:
                return base64.b64decode(val)
            except Exception:
                return val.encode("utf-8")
        return None

    # 1. Direct output_video attribute (Standard on Interaction)
    out_video = getattr(interaction, "output_video", None)
    if out_video:
        b = _to_bytes(getattr(out_video, "data", None)) or _to_bytes(getattr(out_video, "video_bytes", None))
        if b:
            return b

    # 2. Iterate through interaction.steps
    steps = getattr(interaction, "steps", None) or []
    for step in steps:
        step_out = getattr(step, "output", None) or []
        for item in step_out:
            b = _to_bytes(getattr(item, "data", None)) or _to_bytes(getattr(item, "video_bytes", None))
            if b:
                return b

    # 3. Iterate through interaction.outputs (if present on certain versions)
    outputs = getattr(interaction, "outputs", None) or []
    for step in outputs:
        step_out = getattr(step, "output", None) or []
        for item in step_out:
            b = _to_bytes(getattr(item, "data", None)) or _to_bytes(getattr(item, "video_bytes", None))
            if b:
                return b

    # 4. model_dump dictionary traversal
    if hasattr(interaction, "model_dump"):
        try:
            dump = interaction.model_dump()
            dump_vid = dump.get("output_video")
            if dump_vid:
                b = _to_bytes(dump_vid.get("data")) or _to_bytes(dump_vid.get("video_bytes"))
                if b:
                    return b

            for step in dump.get("steps", []) + dump.get("outputs", []):
                for item in step.get("output", []):
                    b = _to_bytes(item.get("data")) or _to_bytes(item.get("video_bytes"))
                    if b:
                        return b
        except Exception:
            pass

    # 5. Raw __dict__ traversal
    if hasattr(interaction, "__dict__"):
        try:
            d = interaction.__dict__
            d_vid = d.get("output_video")
            if d_vid:
                b = _to_bytes(getattr(d_vid, "data", None) if not isinstance(d_vid, dict) else d_vid.get("data")) or \
                    _to_bytes(getattr(d_vid, "video_bytes", None) if not isinstance(d_vid, dict) else d_vid.get("video_bytes"))
                if b:
                    return b
        except Exception:
            pass

    return None


async def generate_incident_diagnostic_video(
    prompt: str,
    tool_context: ToolContext,
    aspect_ratio: str = "16:9"
) -> str:
    """
    Generates a short animated diagnostic or simulation video for an incident scenario,
    technical failure, or architectural component in the Site Reliability / Incident domain
    using Google's Omni model (gemini-omni-flash-preview) in the global region.

    Saves the video artifact with tool_context.save_artifact so it shows up in Playground's
    Artifacts panel, and uploads the video bytes to the public Cloud Storage bucket, returning
    its public HTTPS URL.

    Args:
        prompt: Description of the incident animation/diagnostic video to generate (e.g. "Kubernetes OOMKilled pod crashloop animation", "Kafka broker partition lag spike diagram").
        tool_context: The ADK tool context providing artifact persistence.
        aspect_ratio: Either '16:9' or '9:16'. Default is '16:9'.

    Returns:
        A JSON string containing the public Cloud Storage HTTPS URL, artifact filename, and status.
    """
    try:
        logger.info(f"Generating incident diagnostic video with prompt: '{prompt}'")
        client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location="global"
        )

        response_format: Dict[str, Any] = {
            "type": "video",
            "delivery": "inline",
            "aspect_ratio": aspect_ratio if aspect_ratio in ["16:9", "9:16"] else "16:9"
        }

        interaction = client.interactions.create(
            model=OMNI_MODEL_NAME,
            input=f"Create a short technical diagnostic and visual incident animation showing: {prompt}",
            response_format=response_format,
            timeout=180.0
        )

        video_bytes = _extract_video_bytes(interaction)
        if not video_bytes:
            raise ValueError(f"No video bytes returned by {OMNI_MODEL_NAME} interaction.")

        # 1. Save artifact to Playground's Artifacts panel
        filename = f"incident_video_{uuid.uuid4().hex[:8]}.mp4"
        artifact_part = genai_types.Part.from_bytes(data=video_bytes, mime_type="video/mp4")
        await tool_context.save_artifact(
            filename=filename,
            artifact=artifact_part,
            custom_metadata={"prompt": prompt, "model": OMNI_MODEL_NAME}
        )
        logger.info(f"Saved artifact to tool_context: {filename}")

        # 2. Upload video bytes to public Cloud Storage bucket
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(f"videos/{filename}")
        blob.upload_from_string(video_bytes, content_type="video/mp4")

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/videos/{filename}"
        logger.info(f"Uploaded video to Cloud Storage: {public_url}")

        import json
        return json.dumps({
            "status": "success",
            "message": f"Generated incident diagnostic video successfully using {OMNI_MODEL_NAME}.",
            "artifact_filename": filename,
            "public_url": public_url,
            "prompt": prompt,
            "video_size_bytes": len(video_bytes)
        }, indent=2)

    except Exception as e:
        logger.exception("Error in generate_incident_diagnostic_video")
        return f"Error generating video: {str(e)}"
