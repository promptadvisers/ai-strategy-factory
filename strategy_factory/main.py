"""
CLI entry point for the AI Strategy Factory.

Provides commands for:
- run: Execute full pipeline (research → synthesis → generation)
- resume: Continue from checkpoint
- status: Show progress for a company
- reset: Clear progress and start fresh
"""

import argparse
import sys
import os
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from strategy_factory.config import OUTPUT_DIR, DELIVERABLES
from strategy_factory.models import (
    CompanyInput,
    ResearchMode,
    ResearchOutput,
    DeliverableStatus,
)
from strategy_factory.progress_tracker import ProgressTracker, slugify
from strategy_factory.research.orchestrator import ResearchOrchestrator
from strategy_factory.synthesis.orchestrator import SynthesisOrchestrator
from strategy_factory.generation.orchestrator import GenerationOrchestrator


# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class StrategyFactoryCLI:
    """
    Main CLI class for the AI Strategy Factory.
    """

    def __init__(self):
        self.parser = self._create_parser()

    def _create_parser(self) -> argparse.ArgumentParser:
        """Create the argument parser with all commands."""
        parser = argparse.ArgumentParser(
            prog="strategy_factory",
            description="AI Strategy Factory - Generate comprehensive AI strategy deliverables",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  # Run full pipeline for a company (quick mode)
  python -m strategy_factory.main run "Acme Corp"

  # Run with comprehensive research
  python -m strategy_factory.main run "Acme Corp" --mode comprehensive

  # Run with additional context
  python -m strategy_factory.main run "Acme Corp" --context "Healthcare tech startup, 50 employees"

  # Dry run to see what would be generated
  python -m strategy_factory.main run "Acme Corp" --dry-run

  # Resume from checkpoint
  python -m strategy_factory.main resume "Acme Corp"

  # Check status
  python -m strategy_factory.main status "Acme Corp"

  # Reset progress
  python -m strategy_factory.main reset "Acme Corp"
""",
        )

        subparsers = parser.add_subparsers(dest="command", help="Available commands")

        # Run command
        run_parser = subparsers.add_parser(
            "run", help="Run full pipeline for a company"
        )
        run_parser.add_argument(
            "company", type=str, help="Company name"
        )
        run_parser.add_argument(
            "--context", "-c",
            type=str,
            default="",
            help="Additional context about the company (industry, size, goals, etc.)",
        )
        run_parser.add_argument(
            "--mode", "-m",
            type=str,
            choices=["quick", "comprehensive"],
            default="quick",
            help="Research mode: quick (~$0.05) or comprehensive (~$0.30-0.80)",
        )
        run_parser.add_argument(
            "--industry", "-i",
            type=str,
            default="",
            help="Company industry (optional, will be detected if not provided)",
        )
        run_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be generated without making API calls",
        )
        run_parser.add_argument(
            "--skip-research",
            action="store_true",
            help="Skip research phase (use cached research if available)",
        )
        run_parser.add_argument(
            "--skip-synthesis",
            action="store_true",
            help="Skip synthesis phase (use cached deliverables if available)",
        )
        run_parser.add_argument(
            "--skip-generation",
            action="store_true",
            help="Skip final document generation (PPTX, DOCX)",
        )
        run_parser.add_argument(
            "--verbose", "-v",
            action="store_true",
            help="Enable verbose output",
        )

        # Resume command
        resume_parser = subparsers.add_parser(
            "resume", help="Resume pipeline from checkpoint"
        )
        resume_parser.add_argument(
            "company", type=str, help="Company name"
        )
        resume_parser.add_argument(
            "--verbose", "-v",
            action="store_true",
            help="Enable verbose output",
        )

        # Status command
        status_parser = subparsers.add_parser(
            "status", help="Show progress for a company"
        )
        status_parser.add_argument(
            "company", type=str, help="Company name"
        )
        status_parser.add_argument(
            "--detailed", "-d",
            action="store_true",
            help="Show detailed deliverable status",
        )

        # Reset command
        reset_parser = subparsers.add_parser(
            "reset", help="Reset progress for a company"
        )
        reset_parser.add_argument(
            "company", type=str, help="Company name"
        )
        reset_parser.add_argument(
            "--keep-research",
            action="store_true",
            help="Keep research cache when resetting",
        )
        reset_parser.add_argument(
            "--yes", "-y",
            action="store_true",
            help="Skip confirmation prompt",
        )

        # List command
        list_parser = subparsers.add_parser(
            "list", help="List all companies with progress"
        )

        return parser

    def run(self, args: Optional[list] = None):
        """Run the CLI with given arguments."""
        parsed = self.parser.parse_args(args)

        if not parsed.command:
            self.parser.print_help()
            return 1

        # Dispatch to command handler
        handler = getattr(self, f"cmd_{parsed.command}", None)
        if handler:
            return handler(parsed)
        else:
            print(f"Unknown command: {parsed.command}")
            return 1

    # ========================================================================
    # Command Handlers
    # ========================================================================

    def cmd_run(self, args) -> int:
        """Handle the 'run' command."""
        company_name = args.company
        mode = ResearchMode.QUICK if args.mode == "quick" else ResearchMode.COMPREHENSIVE

        if args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)

        print(f"\n{'='*60}")
        print(f"AI Strategy Factory")
        print(f"{'='*60}")
        print(f"Company: {company_name}")
        print(f"Mode: {args.mode}")
        if args.context:
            print(f"Context: {args.context[:100]}{'...' if len(args.context) > 100 else ''}")
        print(f"{'='*60}\n")

        # Dry run mode
        if args.dry_run:
            return self._dry_run(company_name, mode, args.context, args.industry)

        # Check for API keys
        if not self._check_api_keys():
            return 1

        # Create company input
        company_input = CompanyInput(
            name=company_name,
            context=args.context,
            mode=mode,
            industry=args.industry or None,
        )

        # Create progress tracker
        tracker = ProgressTracker(company_name, company_input)

        try:
            # Phase 1: Research
            if not args.skip_research:
                research_output = self._run_research(tracker, company_input, mode)
                if not research_output:
                    return 1
            else:
                research_output = tracker.load_research_output()
                if not research_output:
                    print("Error: No cached research found. Remove --skip-research flag.")
                    return 1
                print("Using cached research data.")

            # Phase 2: Synthesis
            if not args.skip_synthesis:
                synthesis_output = self._run_synthesis(tracker, company_input, research_output)
                if not synthesis_output:
                    return 1
            else:
                # Load from existing files
                print("Skipping synthesis phase (using cached deliverables).")
                synthesis_output = None

            # Phase 3: Document Generation
            if not args.skip_generation and synthesis_output:
                result = self._run_generation(tracker, company_input, research_output, synthesis_output)
                if not result:
                    return 1

            # Print final summary
            self._print_final_summary(tracker)
            return 0

        except KeyboardInterrupt:
            print("\n\nInterrupted by user. Progress has been saved.")
            tracker.print_status()
            return 130

        except Exception as e:
            logger.exception(f"Error during pipeline execution: {e}")
            print(f"\nError: {e}")
            print("Progress has been saved. Use 'resume' to continue.")
            return 1

    def cmd_resume(self, args) -> int:
        """Handle the 'resume' command."""
        company_name = args.company
        company_slug = slugify(company_name)
        state_file = OUTPUT_DIR / company_slug / "state.json"

        if not state_file.exists():
            print(f"Error: No existing progress found for '{company_name}'.")
            print(f"Expected state file: {state_file}")
            print("Use 'run' command to start a new pipeline.")
            return 1

        if args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)

        # Load existing tracker
        tracker = ProgressTracker(company_name)
        company_input = tracker.state.input_data
        mode = company_input.mode

        print(f"\n{'='*60}")
        print(f"AI Strategy Factory - Resume")
        print(f"{'='*60}")
        print(f"Company: {company_name}")
        print(f"Mode: {mode.value}")
        print(f"Current Phase: {tracker.state.current_phase}")
        print(f"{'='*60}\n")

        # Check for API keys
        if not self._check_api_keys():
            return 1

        try:
            # Determine where to resume
            research_output = tracker.load_research_output()
            current_phase = tracker.state.current_phase

            # Resume research if needed
            if current_phase == "research" or not research_output:
                print("Resuming from research phase...")
                research_output = self._run_research(tracker, company_input, mode)
                if not research_output:
                    return 1

            # Check if synthesis is complete
            completed_deliverables = tracker.get_completed_deliverables()
            markdown_deliverables = [
                d_id for d_id, config in DELIVERABLES.items()
                if config.get("format") == "markdown"
            ]
            all_markdown_done = all(d in completed_deliverables for d in markdown_deliverables)

            # Resume synthesis if needed
            if not all_markdown_done:
                print("Resuming synthesis phase...")
                synthesis_output = self._run_synthesis(tracker, company_input, research_output)
                if not synthesis_output:
                    return 1
            else:
                print("Synthesis already complete.")
                # Reconstruct synthesis output from files
                synthesis_output = self._load_synthesis_from_files(tracker)

            # Check if final documents are generated
            final_deliverables = ["executive_summary_deck", "full_findings_presentation",
                                  "final_strategy_report", "statement_of_work"]
            final_done = all(d in completed_deliverables for d in final_deliverables)

            if not final_done and synthesis_output:
                print("Resuming document generation phase...")
                result = self._run_generation(tracker, company_input, research_output, synthesis_output)
                if not result:
                    return 1
            else:
                print("Document generation already complete.")

            # Print final summary
            self._print_final_summary(tracker)
            return 0

        except KeyboardInterrupt:
            print("\n\nInterrupted by user. Progress has been saved.")
            tracker.print_status()
            return 130

        except Exception as e:
            logger.exception(f"Error during pipeline execution: {e}")
            print(f"\nError: {e}")
            print("Progress has been saved. Use 'resume' to continue.")
            return 1

    def cmd_status(self, args) -> int:
        """Handle the 'status' command."""
        company_name = args.company
        company_slug = slugify(company_name)
        state_file = OUTPUT_DIR / company_slug / "state.json"

        if not state_file.exists():
            print(f"No progress found for '{company_name}'.")
            return 1

        tracker = ProgressTracker(company_name)
        tracker.print_status()

        if args.detailed:
            print("\nDeliverable Details:")
            print("-" * 60)
            for d_id, d_progress in tracker.state.deliverables.items():
                config = DELIVERABLES.get(d_id, {})
                name = config.get("name", d_id)
                status_icon = {
                    DeliverableStatus.COMPLETED: "✓",
                    DeliverableStatus.IN_PROGRESS: "◑",
                    DeliverableStatus.FAILED: "✗",
                    DeliverableStatus.PENDING: "○",
                    DeliverableStatus.SKIPPED: "−",
                }.get(d_progress.status, "?")

                print(f"  {status_icon} {name}")
                if d_progress.file_path:
                    print(f"      Path: {d_progress.file_path}")
                if d_progress.error:
                    print(f"      Error: {d_progress.error[:50]}...")

        return 0

    def cmd_reset(self, args) -> int:
        """Handle the 'reset' command."""
        company_name = args.company
        company_slug = slugify(company_name)
        output_dir = OUTPUT_DIR / company_slug

        if not output_dir.exists():
            print(f"No progress found for '{company_name}'.")
            return 1

        # Confirmation
        if not args.yes:
            keep_research_msg = " (research will be kept)" if args.keep_research else ""
            confirm = input(f"Reset all progress for '{company_name}'{keep_research_msg}? [y/N] ")
            if confirm.lower() not in ["y", "yes"]:
                print("Cancelled.")
                return 0

        tracker = ProgressTracker(company_name)
        tracker.reset(keep_research=args.keep_research)

        print(f"Progress reset for '{company_name}'.")
        if args.keep_research:
            print("Research cache has been preserved.")
        return 0

    def cmd_list(self, args) -> int:
        """Handle the 'list' command."""
        if not OUTPUT_DIR.exists():
            print("No companies found. Output directory does not exist.")
            return 0

        companies = []
        for item in OUTPUT_DIR.iterdir():
            if item.is_dir() and (item / "state.json").exists():
                tracker = ProgressTracker(item.name)
                summary = tracker.get_progress_summary()
                companies.append({
                    "name": summary["company_name"],
                    "slug": item.name,
                    "phase": summary["current_phase"],
                    "progress": summary["deliverables"]["progress_percent"],
                    "cost": summary["costs"]["total"],
                })

        if not companies:
            print("No companies found.")
            return 0

        print(f"\n{'='*70}")
        print(f"{'Company':<30} {'Phase':<15} {'Progress':<12} {'Cost':>8}")
        print(f"{'='*70}")

        for c in sorted(companies, key=lambda x: x["name"]):
            print(f"{c['name']:<30} {c['phase']:<15} {c['progress']:>6.1f}% {c['cost']:>8.4f}")

        print(f"{'='*70}\n")
        return 0

    # ========================================================================
    # Pipeline Execution Methods
    # ========================================================================

    def _run_research(
        self,
        tracker: ProgressTracker,
        company_input: CompanyInput,
        mode: ResearchMode,
    ) -> Optional[ResearchOutput]:
        """Execute the research phase."""
        print("Phase 1: Research")
        print("-" * 40)

        tracker.start_phase("research")

        def progress_callback(message: str, progress: float):
            bar_length = 30
            filled = int(bar_length * progress)
            bar = "█" * filled + "░" * (bar_length - filled)
            print(f"\r  [{bar}] {progress*100:5.1f}% - {message:<40}", end="", flush=True)

        try:
            orchestrator = ResearchOrchestrator(
                mode=mode,
                cache_dir=Path(tracker.output_dir),
                progress_callback=progress_callback,
            )

            research_output = orchestrator.research(company_input)
            print()  # New line after progress bar

            # Save research output
            tracker.save_research_output(research_output)
            orchestrator.save_research_cache(Path(tracker.output_dir))

            # Complete phase
            cost_summary = orchestrator.get_cost_summary()
            summary = f"Completed {len(orchestrator.results)} queries. Cost: ${cost_summary['total_cost']:.4f}"
            tracker.complete_phase("research", summary)

            print(f"\n  ✓ Research complete")
            print(f"    Queries: {len(orchestrator.results)}")
            print(f"    Cost: ${cost_summary['total_cost']:.4f}")
            print(f"    Info Tier: {research_output.information_tier.value}")
            print()

            return research_output

        except Exception as e:
            print()
            tracker.fail_phase("research", str(e))
            raise

    def _run_synthesis(
        self,
        tracker: ProgressTracker,
        company_input: CompanyInput,
        research: ResearchOutput,
    ) -> Optional:
        """Execute the synthesis phase."""
        from strategy_factory.synthesis.orchestrator import SynthesisOrchestrator

        print("Phase 2: Synthesis")
        print("-" * 40)

        tracker.start_phase("synthesis")

        def progress_callback(message: str, progress: float):
            bar_length = 30
            filled = int(bar_length * progress)
            bar = "█" * filled + "░" * (bar_length - filled)
            print(f"\r  [{bar}] {progress*100:5.1f}% - {message:<40}", end="", flush=True)

        try:
            orchestrator = SynthesisOrchestrator(
                output_dir=OUTPUT_DIR,
                progress_callback=progress_callback,
            )

            synthesis_output = orchestrator.synthesize(company_input, research)
            print()  # New line after progress bar

            # Save deliverables
            file_paths = orchestrator.save_deliverables(tracker.company_slug)

            # Update tracker for each deliverable
            for d_id, path in file_paths.items():
                tracker.complete_deliverable(d_id, path)

            # Complete phase
            cost_summary = orchestrator.get_cost_summary()
            completed_count = len(orchestrator.generated_content)
            summary = f"Generated {completed_count} deliverables. Cost: ${cost_summary['total_cost']:.4f}"
            tracker.complete_phase("synthesis", summary)
            tracker.add_cost(cost_summary["total_cost"], "synthesis")

            print(f"\n  ✓ Synthesis complete")
            print(f"    Deliverables: {completed_count}")
            print(f"    Cost: ${cost_summary['total_cost']:.4f}")
            if orchestrator.errors:
                print(f"    Errors: {len(orchestrator.errors)}")
            print()

            return synthesis_output

        except Exception as e:
            print()
            tracker.fail_phase("synthesis", str(e))
            raise

    def _run_generation(
        self,
        tracker: ProgressTracker,
        company_input: CompanyInput,
        research: ResearchOutput,
        synthesis,
    ):
        """Execute the document generation phase."""
        print("Phase 3: Document Generation")
        print("-" * 40)

        tracker.start_phase("generation")

        def progress_callback(message: str, progress: float):
            bar_length = 30
            filled = int(bar_length * progress)
            bar = "█" * filled + "░" * (bar_length - filled)
            print(f"\r  [{bar}] {progress*100:5.1f}% - {message:<40}", end="", flush=True)

        try:
            orchestrator = GenerationOrchestrator(
                output_dir=OUTPUT_DIR,
                progress_callback=progress_callback,
            )

            result = orchestrator.generate_all(
                company_slug=tracker.company_slug,
                company_input=company_input,
                research=research,
                synthesis=synthesis,
            )
            print()  # New line after progress bar

            # Update tracker for generated files
            for deliverable in result.deliverables:
                d_name = deliverable["name"]
                d_path = deliverable["path"]
                # Find the deliverable ID from the name
                for d_id, config in DELIVERABLES.items():
                    if config.get("name") == d_name:
                        tracker.complete_deliverable(d_id, d_path)
                        break

            # Persist any generation errors to state.json so failures (e.g. a
            # deck that failed to build) are debuggable later, not just printed.
            for err in orchestrator.get_errors():
                tracker.state.errors.append(err)

            # Complete phase (this saves state, including the errors appended above)
            summary = f"Generated {len(result.deliverables)} files in {result.generation_time:.1f}s"
            tracker.complete_phase("generation", summary)

            print(f"\n  ✓ Generation complete")
            print(f"    Files: {len(result.deliverables)}")
            print(f"    Time: {result.generation_time:.1f}s")
            if result.errors:
                print(f"    Errors: {len(result.errors)}")
            print()

            return result

        except Exception as e:
            print()
            tracker.fail_phase("generation", str(e))
            raise

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _check_api_keys(self) -> bool:
        """Check if required API keys are set."""
        missing = []

        if not os.getenv("PERPLEXITY_API_KEY"):
            missing.append("PERPLEXITY_API_KEY")

        if not os.getenv("GEMINI_API_KEY"):
            missing.append("GEMINI_API_KEY")

        if missing:
            print("Error: Missing required API keys:")
            for key in missing:
                print(f"  - {key}")
            print("\nSet these in your .env file or environment.")
            return False

        return True

    def _dry_run(
        self,
        company_name: str,
        mode: ResearchMode,
        context: str,
        industry: str,
    ) -> int:
        """Perform a dry run without API calls."""
        print("DRY RUN MODE - No API calls will be made\n")

        company_slug = slugify(company_name)
        output_dir = OUTPUT_DIR / company_slug

        print("Pipeline Overview:")
        print("-" * 40)

        # Research phase
        print("\n1. RESEARCH PHASE")
        print(f"   Mode: {mode.value}")
        if mode == ResearchMode.QUICK:
            print("   Queries: ~9 queries (essential info only)")
            print("   Models: sonar")
            print("   Est. Cost: ~$0.01-0.05")
        else:
            print("   Queries: ~18 queries (comprehensive)")
            print("   Models: sonar, sonar-pro, sonar-deep-research")
            print("   Est. Cost: ~$0.30-0.80")

        # Synthesis phase
        print("\n2. SYNTHESIS PHASE")
        markdown_count = len([d for d, c in DELIVERABLES.items() if c.get("format") == "markdown"])
        print(f"   Deliverables: {markdown_count} markdown files")
        print("   Model: gemini-2.5-flash")
        print("   Est. Cost: ~$0.01-0.10")

        # Generation phase
        print("\n3. GENERATION PHASE")
        print("   - 2 PowerPoint presentations")
        print("   - 2 Word documents")
        print("   - Mermaid diagrams (PNG)")

        # Output structure
        print("\nOutput Directory Structure:")
        print(f"  {output_dir}/")
        print(f"    ├── markdown/")
        for d_id, config in DELIVERABLES.items():
            if config.get("format") == "markdown":
                print(f"    │   ├── {d_id}.md")
        print(f"    ├── mermaid_images/")
        print(f"    │   ├── current_state.png")
        print(f"    │   ├── future_state.png")
        print(f"    │   └── data_flow.png")
        print(f"    ├── presentations/")
        print(f"    │   ├── executive_summary.pptx")
        print(f"    │   └── full_findings.pptx")
        print(f"    ├── documents/")
        print(f"    │   ├── final_strategy_report.docx")
        print(f"    │   └── statement_of_work.docx")
        print(f"    ├── state.json")
        print(f"    └── research_cache.json")

        # Total estimates
        print("\nTotal Estimated Cost:")
        if mode == ResearchMode.QUICK:
            print("   Research: ~$0.01-0.05")
            print("   Synthesis: ~$0.01-0.10")
            print("   ─────────────────────")
            print("   Total: ~$0.02-0.15")
        else:
            print("   Research: ~$0.30-0.80")
            print("   Synthesis: ~$0.01-0.10")
            print("   ─────────────────────")
            print("   Total: ~$0.31-0.90")

        print("\nTo execute, remove the --dry-run flag.")
        return 0

    def _load_synthesis_from_files(self, tracker: ProgressTracker):
        """Load synthesis output from existing files."""
        from strategy_factory.models import SynthesisOutput, DeliverableContent

        markdown_dir = Path(tracker.output_dir) / "markdown"
        deliverables = {}

        for d_id, config in DELIVERABLES.items():
            if config.get("format") != "markdown":
                continue

            file_path = markdown_dir / f"{d_id}.md"
            if file_path.exists():
                with open(file_path, "r") as f:
                    content = f.read()

                deliverables[d_id] = DeliverableContent(
                    deliverable_id=d_id,
                    name=config.get("name", d_id),
                    format="markdown",
                    content=content,
                    file_path=str(file_path),
                    generated_at=datetime.now(),
                )

        return SynthesisOutput(
            company_name=tracker.company_name,
            synthesis_timestamp=datetime.now(),
            deliverables=deliverables,
            total_cost=0.0,
        )

    def _print_final_summary(self, tracker: ProgressTracker):
        """Print final summary after pipeline completion."""
        summary = tracker.get_progress_summary()

        print(f"\n{'='*60}")
        print("PIPELINE COMPLETE")
        print(f"{'='*60}")
        print(f"Company: {summary['company_name']}")
        print(f"Output: {tracker.output_dir}")
        print(f"\nDeliverables: {summary['deliverables']['completed']}/{summary['deliverables']['total']}")
        print(f"Total Cost: ${summary['costs']['total']:.4f}")

        if summary['errors']:
            print(f"\nWarnings/Errors: {len(summary['errors'])}")
            for err in summary['errors']:
                print(f"  - {err.get('deliverable') or err.get('phase')}: {err['error'][:60]}...")

        print(f"\nGenerated Files:")
        output_dir = Path(tracker.output_dir)

        # List markdown files
        markdown_dir = output_dir / "markdown"
        if markdown_dir.exists():
            md_files = list(markdown_dir.glob("*.md"))
            print(f"  Markdown: {len(md_files)} files")

        # List presentations
        pres_dir = output_dir / "presentations"
        if pres_dir.exists():
            pptx_files = list(pres_dir.glob("*.pptx"))
            print(f"  Presentations: {len(pptx_files)} files")

        # List documents
        docs_dir = output_dir / "documents"
        if docs_dir.exists():
            docx_files = list(docs_dir.glob("*.docx"))
            print(f"  Documents: {len(docx_files)} files")

        # List mermaid images
        mermaid_dir = output_dir / "mermaid_images"
        if mermaid_dir.exists():
            img_files = list(mermaid_dir.glob("*.png"))
            print(f"  Diagrams: {len(img_files)} images")

        print(f"{'='*60}\n")


def main():
    """Main entry point."""
    cli = StrategyFactoryCLI()
    sys.exit(cli.run())


if __name__ == "__main__":
    main()
