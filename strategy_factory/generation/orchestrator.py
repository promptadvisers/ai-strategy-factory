"""
Generation orchestrator for coordinating all document creation.

Manages the generation of all output formats (markdown, PPTX, DOCX)
and mermaid diagram rendering.
"""

import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any

from ..config import OUTPUT_DIR, DELIVERABLES
from ..models import (
    CompanyInput,
    ResearchOutput,
    SynthesisOutput,
    DeliverableContent,
    GenerationResult,
)

from .markdown_generator import MarkdownGenerator
from .mermaid_renderer import MermaidRenderer
from .pptx_generator import PowerPointGenerator
from .docx_generator import DocxGenerator


class GenerationOrchestrator:
    """
    Orchestrates the generation of all output deliverables.

    Responsibilities:
    - Save markdown files
    - Render mermaid diagrams to images
    - Generate PowerPoint presentations
    - Generate Word documents
    - Track progress and handle errors
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ):
        """
        Initialize the generation orchestrator.

        Args:
            output_dir: Base output directory.
            progress_callback: Callback for progress updates.
        """
        self.output_dir = output_dir or OUTPUT_DIR
        self.progress_callback = progress_callback

        # Initialize generators
        self.markdown_gen = MarkdownGenerator(output_dir=self.output_dir)
        self.mermaid_renderer = MermaidRenderer(output_dir=self.output_dir)
        self.pptx_gen = PowerPointGenerator(output_dir=self.output_dir)
        self.docx_gen = DocxGenerator(output_dir=self.output_dir)

        # Track results
        self.generated_files: Dict[str, str] = {}
        self.errors: List[Dict[str, Any]] = []

    def generate_all(
        self,
        company_slug: str,
        company_input: CompanyInput,
        research: ResearchOutput,
        synthesis: SynthesisOutput,
    ) -> GenerationResult:
        """
        Generate all output deliverables.

        Args:
            company_slug: URL-safe company name.
            company_input: Original company input.
            research: Research output.
            synthesis: Synthesis output.

        Returns:
            GenerationResult with all generated files.
        """
        start_time = datetime.now()
        total_steps = 5  # markdown, mermaid, exec pptx, full pptx, docx (x2)
        current_step = 0

        self._report_progress("Starting generation", 0)

        # Step 1: Save markdown files
        current_step += 1
        self._report_progress("Saving markdown files", current_step / total_steps)
        markdown_paths = self._save_markdown(company_slug, synthesis)
        self.generated_files.update(markdown_paths)

        # Step 2: Render mermaid diagrams
        current_step += 1
        self._report_progress("Rendering mermaid diagrams", current_step / total_steps)
        mermaid_images = self._render_mermaid(company_slug, synthesis)
        self.generated_files.update({
            f"mermaid_{k}": v for k, v in mermaid_images.items()
        })

        # Step 3: Generate PowerPoint presentations
        current_step += 1
        self._report_progress("Generating PowerPoint presentations", current_step / total_steps)
        pptx_paths = self._generate_presentations(
            company_slug, company_input, research, synthesis, mermaid_images
        )
        self.generated_files.update(pptx_paths)

        # Step 4: Generate Word documents
        current_step += 1
        self._report_progress("Generating Word documents", current_step / total_steps)
        docx_paths = self._generate_documents(
            company_slug, company_input, research, synthesis
        )
        self.generated_files.update(docx_paths)

        # Complete
        self._report_progress("Generation complete", 1.0)

        # Calculate duration
        duration = (datetime.now() - start_time).total_seconds()

        # Build result
        deliverables_list = [
            {
                "name": self._get_deliverable_name(key),
                "path": path,
                "format": self._get_format_from_path(path),
            }
            for key, path in self.generated_files.items()
        ]

        return GenerationResult(
            company_name=company_input.name,
            success=len(self.errors) == 0,
            output_dir=str(self.output_dir / company_slug),
            deliverables=deliverables_list,
            total_cost=synthesis.total_cost if synthesis else 0.0,
            generation_time=duration,
            errors=[e["error"] for e in self.errors],
        )

    def _save_markdown(
        self,
        company_slug: str,
        synthesis: SynthesisOutput,
    ) -> Dict[str, str]:
        """Save all markdown deliverables."""
        try:
            return self.markdown_gen.save_all(company_slug, synthesis)
        except Exception as e:
            self._record_error("markdown_generation", str(e))
            return {}

    def _render_mermaid(
        self,
        company_slug: str,
        synthesis: SynthesisOutput,
    ) -> Dict[str, str]:
        """Render mermaid diagrams from markdown content."""
        # Get mermaid diagrams deliverable content
        if "03_mermaid_diagrams" not in synthesis.deliverables:
            return {}

        mermaid_content = synthesis.deliverables["03_mermaid_diagrams"].content
        if not mermaid_content:
            return {}

        try:
            # Extract diagram names from headings
            diagram_names = self._extract_diagram_names(mermaid_content)

            return self.mermaid_renderer.render_from_markdown(
                company_slug=company_slug,
                markdown_content=mermaid_content,
                diagram_names=diagram_names,
            )
        except Exception as e:
            self._record_error("mermaid_rendering", str(e))
            return {}

    def _extract_diagram_names(self, content: str) -> List[str]:
        """Extract diagram names from markdown headings."""
        names = []
        heading_pattern = re.compile(r'^##\s+(.+)$', re.MULTILINE)

        for match in heading_pattern.finditer(content):
            heading = match.group(1).strip()
            # Convert heading to snake_case for filename
            name = re.sub(r'[^a-zA-Z0-9\s]', '', heading)
            name = re.sub(r'\s+', '_', name).lower()
            names.append(name)

        return names if names else ["current_state", "future_state", "data_flow"]

    def _generate_presentations(
        self,
        company_slug: str,
        company_input: CompanyInput,
        research: ResearchOutput,
        synthesis: SynthesisOutput,
        mermaid_images: Dict[str, str],
    ) -> Dict[str, str]:
        """Generate PowerPoint presentations."""
        paths = {}

        # Executive summary deck
        try:
            exec_path = self.pptx_gen.generate_executive_summary(
                company_slug=company_slug,
                company_input=company_input,
                research=research,
                synthesis=synthesis,
                mermaid_images=mermaid_images,
            )
            paths["executive_summary_deck"] = exec_path
        except Exception as e:
            self._record_error("executive_summary_pptx", str(e))

        # Full findings presentation
        try:
            full_path = self.pptx_gen.generate_full_findings(
                company_slug=company_slug,
                company_input=company_input,
                research=research,
                synthesis=synthesis,
                mermaid_images=mermaid_images,
            )
            paths["full_findings_presentation"] = full_path
        except Exception as e:
            self._record_error("full_findings_pptx", str(e))

        return paths

    def _generate_documents(
        self,
        company_slug: str,
        company_input: CompanyInput,
        research: ResearchOutput,
        synthesis: SynthesisOutput,
    ) -> Dict[str, str]:
        """Generate Word documents."""
        paths = {}

        # Final strategy report
        try:
            report_path = self.docx_gen.generate_strategy_report(
                company_slug=company_slug,
                company_input=company_input,
                research=research,
                synthesis=synthesis,
            )
            paths["final_strategy_report"] = report_path
        except Exception as e:
            self._record_error("strategy_report_docx", str(e))

        # Statement of work
        try:
            sow_path = self.docx_gen.generate_statement_of_work(
                company_slug=company_slug,
                company_input=company_input,
                research=research,
                synthesis=synthesis,
            )
            paths["statement_of_work"] = sow_path
        except Exception as e:
            self._record_error("sow_docx", str(e))

        return paths

    def _get_deliverable_name(self, key: str) -> str:
        """Get human-readable name for a deliverable."""
        # Check if it's in DELIVERABLES config
        if key in DELIVERABLES:
            return DELIVERABLES[key]["name"]

        # Handle mermaid images
        if key.startswith("mermaid_"):
            diagram_name = key.replace("mermaid_", "").replace("_", " ").title()
            return f"Mermaid Diagram: {diagram_name}"

        # Fallback: convert key to title case
        return key.replace("_", " ").title()

    def _get_format_from_path(self, path: str) -> str:
        """Get file format from path."""
        path = Path(path)
        suffix = path.suffix.lower()

        format_map = {
            ".md": "markdown",
            ".pptx": "pptx",
            ".docx": "docx",
            ".png": "image",
            ".mmd": "mermaid",
        }

        return format_map.get(suffix, "unknown")

    def _record_error(self, component: str, error: str) -> None:
        """Record an error during generation.

        Captures the active traceback (when called from an ``except`` block) and
        prints immediately, so failures are visible in run logs and persistable
        to state.json instead of being silently swallowed.
        """
        tb = traceback.format_exc()
        if not tb or tb.strip() == "NoneType: None":
            tb = None
        self.errors.append({
            "component": component,
            "error": error,
            "traceback": tb,
            "timestamp": datetime.now().isoformat(),
        })
        print(f"\n  [generation error] {component}: {error}")
        if tb:
            print(tb)

    def _report_progress(self, message: str, progress: float) -> None:
        """Report progress to callback if set."""
        if self.progress_callback:
            self.progress_callback(message, progress)

    def get_generated_files(self) -> Dict[str, str]:
        """Get all generated file paths."""
        return self.generated_files.copy()

    def get_errors(self) -> List[Dict[str, Any]]:
        """Get all errors that occurred during generation."""
        return self.errors.copy()


def run_generation(
    company_slug: str,
    company_input: CompanyInput,
    research: ResearchOutput,
    synthesis: SynthesisOutput,
    output_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> GenerationResult:
    """
    Convenience function to run all generation.

    Args:
        company_slug: URL-safe company name.
        company_input: Original company input.
        research: Research output.
        synthesis: Synthesis output.
        output_dir: Optional output directory.
        progress_callback: Optional progress callback.

    Returns:
        GenerationResult with all generated files.
    """
    orchestrator = GenerationOrchestrator(
        output_dir=output_dir,
        progress_callback=progress_callback,
    )

    return orchestrator.generate_all(
        company_slug=company_slug,
        company_input=company_input,
        research=research,
        synthesis=synthesis,
    )


def generate_outputs_from_synthesis(
    company_slug: str,
    company_input: CompanyInput,
    research: ResearchOutput,
    synthesis: SynthesisOutput,
    output_dir: Optional[Path] = None,
    skip_mermaid: bool = False,
    skip_pptx: bool = False,
    skip_docx: bool = False,
) -> Dict[str, str]:
    """
    Generate specific outputs with fine-grained control.

    Args:
        company_slug: URL-safe company name.
        company_input: Original company input.
        research: Research output.
        synthesis: Synthesis output.
        output_dir: Optional output directory.
        skip_mermaid: Skip mermaid diagram rendering.
        skip_pptx: Skip PowerPoint generation.
        skip_docx: Skip Word document generation.

    Returns:
        Dict mapping deliverable names to file paths.
    """
    base_dir = output_dir or OUTPUT_DIR
    generated = {}

    # Always save markdown
    md_gen = MarkdownGenerator(output_dir=base_dir)
    markdown_paths = md_gen.save_all(company_slug, synthesis)
    generated.update(markdown_paths)

    # Mermaid diagrams
    mermaid_images = {}
    if not skip_mermaid:
        if "03_mermaid_diagrams" in synthesis.deliverables:
            content = synthesis.deliverables["03_mermaid_diagrams"].content
            if content:
                renderer = MermaidRenderer(output_dir=base_dir)
                mermaid_images = renderer.render_from_markdown(
                    company_slug, content
                )
                generated.update({f"mermaid_{k}": v for k, v in mermaid_images.items()})

    # PowerPoint
    if not skip_pptx:
        pptx_gen = PowerPointGenerator(output_dir=base_dir)

        try:
            exec_path = pptx_gen.generate_executive_summary(
                company_slug, company_input, research, synthesis, mermaid_images
            )
            generated["executive_summary_deck"] = exec_path
        except Exception:
            pass

        try:
            full_path = pptx_gen.generate_full_findings(
                company_slug, company_input, research, synthesis, mermaid_images
            )
            generated["full_findings_presentation"] = full_path
        except Exception:
            pass

    # Word documents
    if not skip_docx:
        docx_gen = DocxGenerator(output_dir=base_dir)

        try:
            report_path = docx_gen.generate_strategy_report(
                company_slug, company_input, research, synthesis
            )
            generated["final_strategy_report"] = report_path
        except Exception:
            pass

        try:
            sow_path = docx_gen.generate_statement_of_work(
                company_slug, company_input, research, synthesis
            )
            generated["statement_of_work"] = sow_path
        except Exception:
            pass

    return generated
