"""
Per-JD resume tailoring.

Deliberately bounded: the LLM call below selects, reorders, and lightly
rewords content FROM content_library.json only. It is never asked to invent
an employer, a date, a title, or a quantitative claim -- the tool-use schema
only lets it pick indices/names out of the provided library and write short
framing text, not free-form resume content. The employer bullets themselves
(JPMorgan Chase, Highway) are never touched -- only the tagline, profile
summary, and PROJECTS section vary per job description, pulled from the
existing multi-variant resume library described in content_library.json.

The master template is always read fresh from ~/resumes/Nathan_Johnson_Resume.docx
and never mutated -- every run works on its own temp copy.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

import anthropic

HERE = Path(__file__).parent
MASTER_TEMPLATE = Path.home() / "resumes" / "Nathan_Johnson_Resume.docx"
OUTPUT_DIR = HERE / "output" / "resumes"
CONTENT_LIBRARY = json.loads((HERE / "content_library.json").read_text())

ANTHROPIC_MODEL = "claude-sonnet-5"

TAILOR_TOOL = {
    "name": "select_resume_content",
    "description": (
        "Select and lightly frame resume content for a specific job description, "
        "choosing ONLY from the provided library. Never invent facts not present "
        "in the library."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tagline_index": {
                "type": "integer",
                "description": "Index into tagline_options — which framing best matches this JD.",
            },
            "profile_index": {
                "type": "integer",
                "description": "Index into profile_summary_options — which framing best matches this JD.",
            },
            "selected_projects": {
                "type": "array",
                "description": "2 to 4 project names, in priority order, EXACTLY matching a 'name' field from the projects array. Most JD-relevant first.",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 4,
            },
            "project_framing_notes": {
                "type": "object",
                "description": "Optional: for each selected project name, a short (<=15 word) reason it's relevant to this JD, used only for the internal audit log, not printed on the resume.",
                "additionalProperties": {"type": "string"},
            },
            "reasoning": {
                "type": "string",
                "description": "1-3 sentences on why this selection fits the JD. Internal audit log only.",
            },
        },
        "required": ["tagline_index", "profile_index", "selected_projects", "reasoning"],
    },
}


def _client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic(api_key=api_key)


def select_content_for_jd(jd_text: str, company: str, role_title: str) -> dict:
    """One tool-forced call: pick tagline/profile framing + up to 4 projects
    from content_library.json for this specific job description."""
    library_for_prompt = {
        "tagline_options": CONTENT_LIBRARY["tagline_options"],
        "profile_summary_options": CONTENT_LIBRARY["profile_summary_options"],
        "projects": [
            {"name": p["name"], "stack": p["stack"], "tags": p["tags"]}
            for p in CONTENT_LIBRARY["projects"]
        ],
    }
    system = (
        "You are selecting resume content for Nathan Johnson from a fixed library. "
        "You may ONLY choose indices/names that exist in the library provided below. "
        "Do not invent, exaggerate, or add any fact not present in the library. "
        "Pick the tagline and profile summary that best match the target role, and "
        "the 2-4 most relevant projects for this specific job description, most "
        "relevant first."
    )
    user = (
        f"Company: {company}\nRole: {role_title}\n\n"
        f"Job description:\n{jd_text}\n\n"
        f"Content library:\n{json.dumps(library_for_prompt, indent=2)}"
    )
    resp = _client().messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        system=system,
        tools=[TAILOR_TOOL],
        tool_choice={"type": "tool", "name": "select_resume_content"},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("model did not return a tool_use block")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _replace_text(xml: str, old: str, new: str) -> str:
    old_e, new_e = _esc(old), _esc(new)
    marker = f'<w:t xml:space="preserve">{old_e}</w:t>'
    replacement = f'<w:t xml:space="preserve">{new_e}</w:t>'
    count = xml.count(marker)
    if count != 1:
        # Fall back: try without xml:space="preserve" (short strings sometimes lack it)
        marker2 = f"<w:t>{old_e}</w:t>"
        if xml.count(marker2) == 1:
            return xml.replace(marker2, f"<w:t>{new_e}</w:t>", 1)
        raise RuntimeError(f"expected exactly 1 match for tagline/profile replace, got {count}: {old[:60]}...")
    return xml.replace(marker, replacement, 1)


def build_tailored_resume(jd_text: str, company: str, role_title: str) -> dict:
    """Full pipeline: select content, patch docx, render PDF.
    Returns {docx_path, pdf_path, selection} for the caller to attach + log."""
    selection = select_content_for_jd(jd_text, company, role_title)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", f"{company}_{role_title}").strip("_")[:60]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = OUTPUT_DIR / f"_work_{slug}_{stamp}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    with zipfile.ZipFile(MASTER_TEMPLATE) as z:
        z.extractall(work_dir)

    doc_path = work_dir / "word" / "document.xml"
    xml = doc_path.read_text(encoding="utf-8")

    # --- tagline + profile: template always starts from option[0] (what's
    # baked into the master docx), so the "old" side of the replace is
    # always option[0], regardless of which index the model picked. ---
    tagline_new = CONTENT_LIBRARY["tagline_options"][selection["tagline_index"]]
    if tagline_new != CONTENT_LIBRARY["tagline_options"][0]:
        xml = _replace_text(xml, CONTENT_LIBRARY["tagline_options"][0], tagline_new)

    profile_new = CONTENT_LIBRARY["profile_summary_options"][selection["profile_index"]]
    if profile_new != CONTENT_LIBRARY["profile_summary_options"][0]:
        xml = _replace_text(xml, CONTENT_LIBRARY["profile_summary_options"][0], profile_new)

    # --- PROJECTS section: rebuild entirely from selected projects ---
    projects_by_name = {p["name"]: p for p in CONTENT_LIBRARY["projects"]}
    selected = [projects_by_name[n] for n in selection["selected_projects"] if n in projects_by_name]
    if not selected:
        raise RuntimeError(f"none of the selected project names matched the library: {selection['selected_projects']}")

    p_idx = xml.find(">PROJECTS<")
    e_idx = xml.find(">EXPERIENCE<")
    if p_idx == -1 or e_idx == -1:
        raise RuntimeError("could not locate PROJECTS/EXPERIENCE section headers in template")
    p_end = xml.find("</w:p>", p_idx) + len("</w:p>")
    e_start = xml.rfind("<w:p ", 0, e_idx)

    template_para = (
        '<w:p w:rsidR="00000000" w:rsidDel="00000000" w:rsidP="00000000" w:rsidRDefault="00000000" '
        'w:rsidRPr="00000000" w14:paraId="PARAID"><w:pPr><w:spacing w:after="40" w:before="40" w:lineRule="auto"/>'
        '<w:ind w:left="0" w:firstLine="0"/><w:rPr><w:rFonts w:ascii="Calibri" w:cs="Calibri" w:eastAsia="Calibri" '
        'w:hAnsi="Calibri"/><w:b w:val="1"/><w:bCs w:val="1"/><w:color w:val="4b5563"/><w:sz w:val="18"/>'
        '<w:szCs w:val="18"/></w:rPr></w:pPr><w:r w:rsidDel="00000000" w:rsidR="00000000" w:rsidRPr="00000000">'
        '<w:rPr><w:rFonts w:ascii="Calibri" w:cs="Calibri" w:eastAsia="Calibri" w:hAnsi="Calibri"/>'
        '<w:b w:val="1"/><w:bCs w:val="1"/><w:color w:val="4b5563"/><w:sz w:val="18"/><w:szCs w:val="18"/>'
        '<w:rtl w:val="0"/></w:rPr><w:t xml:space="preserve">TEXT</w:t></w:r></w:p>'
    )

    lines: list[str] = []
    for proj in selected:
        lines.append(f"{proj['name']} — {proj['stack']}")
        lines.append(proj["description"])
        if proj.get("link"):
            lines.append(f"GitHub: {proj['link']}")

    new_paras = []
    for i, line in enumerate(lines):
        para = template_para.replace("PARAID", f"00C{i:05X}").replace("TEXT", _esc(line))
        new_paras.append(para)

    xml = xml[:p_end] + "".join(new_paras) + xml[e_start:]
    doc_path.write_text(xml, encoding="utf-8")

    out_docx = OUTPUT_DIR / f"Nathan_Johnson_Resume_{slug}_{stamp}.docx"
    with zipfile.ZipFile(out_docx, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(work_dir):
            for f in files:
                full = os.path.join(root, f)
                arc = os.path.relpath(full, work_dir)
                zf.write(full, arc)

    out_pdf = out_docx.with_suffix(".pdf")
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(OUTPUT_DIR), str(out_docx)],
        check=True,
        capture_output=True,
        timeout=60,
    )

    shutil.rmtree(work_dir)

    return {"docx_path": str(out_docx), "pdf_path": str(out_pdf), "selection": selection}
