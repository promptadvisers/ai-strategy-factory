"""
Mermaid diagram renderer.

Converts mermaid code blocks from markdown into PNG images
using mermaid-cli (mmdc).
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any
import shutil

from ..config import OUTPUT_DIR


class MermaidRenderer:
    """
    Renders mermaid diagrams to PNG images.

    Uses mermaid-cli (mmdc) for rendering. Falls back to placeholder
    images if mmdc is not available.

    Responsibilities:
    - Extract mermaid code blocks from markdown
    - Render diagrams to PNG using mmdc
    - Handle rendering failures gracefully
    - Manage output file naming
    """

    # Default mermaid config for consistent styling
    DEFAULT_CONFIG = {
        "theme": "default",
        "themeVariables": {
            "primaryColor": "#1f4e79",
            "primaryTextColor": "#ffffff",
            "primaryBorderColor": "#1f4e79",
            "lineColor": "#333333",
            "secondaryColor": "#e6f2ff",
            "tertiaryColor": "#f5f5f5",
            "fontFamily": "Arial, sans-serif",
        },
        "flowchart": {
            "htmlLabels": True,
            "curve": "basis",
        },
    }

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        config: Optional[Dict] = None,
    ):
        """
        Initialize the mermaid renderer.

        Args:
            output_dir: Base output directory.
            config: Optional mermaid config override.
        """
        self.output_dir = output_dir or OUTPUT_DIR
        self.config = config or self.DEFAULT_CONFIG
        self._mmdc_available = self._check_mmdc()

    def _check_mmdc(self) -> bool:
        """Check if mermaid-cli (mmdc) is available."""
        return shutil.which("mmdc") is not None

    def _mmdc_command(self, args: List[str]) -> List[str]:
        """Build a platform-correct command for invoking mmdc.

        On Windows, ``mmdc`` is installed as ``mmdc.cmd`` -- a batch shim that
        ``CreateProcess`` cannot launch directly, so a bare
        ``subprocess.run(["mmdc", ...])`` raises ``FileNotFoundError``
        (``[WinError 2]``) even when mermaid-cli is on PATH. Route the call
        through ``cmd.exe`` so PATHEXT is honored.
        """
        mmdc = shutil.which("mmdc") or "mmdc"
        if os.name == "nt":
            return ["cmd", "/c", mmdc, *args]
        return [mmdc, *args]

    def render_from_markdown(
        self,
        company_slug: str,
        markdown_content: str,
        diagram_names: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """
        Extract and render all mermaid diagrams from markdown.

        Args:
            company_slug: URL-safe company name.
            markdown_content: Markdown content with mermaid blocks.
            diagram_names: Optional list of names for diagrams
                          (in order of appearance).

        Returns:
            Dict mapping diagram name to file path.
        """
        # Extract mermaid blocks
        blocks = self._extract_mermaid_blocks(markdown_content)

        if not blocks:
            return {}

        # Create output directory
        images_dir = self.output_dir / company_slug / "mermaid_images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # Default diagram names based on content
        default_names = [
            "current_state",
            "future_state",
            "data_flow",
            "architecture",
            "process_flow",
        ]

        rendered_paths = {}

        for i, block in enumerate(blocks):
            # Determine diagram name
            if diagram_names and i < len(diagram_names):
                name = diagram_names[i]
            elif i < len(default_names):
                name = default_names[i]
            else:
                name = f"diagram_{i + 1}"

            # Sanitize name for filename
            safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())
            output_path = images_dir / f"{safe_name}.png"

            # Render diagram
            success = self.render_diagram(
                mermaid_code=block["code"],
                output_path=output_path,
            )

            if success:
                rendered_paths[name] = str(output_path)

        return rendered_paths

    def _sanitize_mermaid_code(self, code: str) -> str:
        """
        Sanitize mermaid code to fix common syntax issues.

        Mermaid has trouble with:
        - Parentheses in node labels (interpreted as special nodes)
        - Certain special characters
        """
        lines = code.split('\n')
        sanitized_lines = []

        for line in lines:
            # Fix node definitions with parentheses in the label
            # Pattern: ID[text with (parens)] -> ID["text with parens"]
            # But be careful not to break actual mermaid syntax

            # Look for node definitions: ID[Label with (text)]
            # Replace parentheses within square brackets with quotes around the label

            # Match patterns like: ABC[Some Text (with parens)]
            import re

            # If line contains [] with () inside, quote the label
            match = re.search(r'(\w+)\[([^\]]*\([^)]*\)[^\]]*)\]', line)
            if match:
                node_id = match.group(1)
                label = match.group(2)
                # Remove parentheses from label or replace with dashes
                clean_label = label.replace('(', ' - ').replace(')', '')
                clean_label = clean_label.replace('  ', ' ').strip()
                line = line.replace(f'{node_id}[{label}]', f'{node_id}["{clean_label}"]')

            # Also fix patterns like: (text with (nested parens))
            # For subgraph titles and such
            if 'subgraph' in line and '(' in line:
                # Quote the subgraph title if it contains parens
                match = re.search(r'subgraph\s+"?([^"\n]+\([^)]+\)[^"\n]*)"?', line)
                if match:
                    title = match.group(1)
                    clean_title = title.replace('(', ' - ').replace(')', '')
                    line = line.replace(title, f'"{clean_title}"')

            sanitized_lines.append(line)

        return '\n'.join(sanitized_lines)

    def render_diagram(
        self,
        mermaid_code: str,
        output_path: Path,
        width: int = 1200,
        height: int = 800,
        background: str = "white",
    ) -> bool:
        """
        Render a single mermaid diagram to PNG.

        Args:
            mermaid_code: Mermaid diagram code.
            output_path: Path to save PNG.
            width: Image width in pixels.
            height: Image height in pixels.
            background: Background color.

        Returns:
            True if rendering succeeded.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Sanitize mermaid code to fix common syntax issues
        mermaid_code = self._sanitize_mermaid_code(mermaid_code)

        # Re-check mmdc availability (in case it was installed after init)
        if not self._mmdc_available:
            self._mmdc_available = self._check_mmdc()

        if not self._mmdc_available:
            # Create placeholder file with message
            print("mmdc not available, creating placeholder")
            self._create_placeholder(output_path, mermaid_code)
            return True

        try:
            # Create temp files for input and config
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.mmd',
                delete=False,
            ) as mmd_file:
                mmd_file.write(mermaid_code)
                mmd_path = mmd_file.name

            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.json',
                delete=False,
            ) as config_file:
                json.dump(self.config, config_file)
                config_path = config_file.name

            try:
                # Run mmdc - use simpler command without puppeteerConfigFile
                cmd = self._mmdc_command([
                    "-i", mmd_path,
                    "-o", str(output_path),
                    "-c", config_path,
                    "-w", str(width),
                    "-H", str(height),
                    "-b", background,
                ])

                print(f"Running: {' '.join(cmd)}")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,  # Increased timeout for complex diagrams
                )

                if result.returncode != 0:
                    print(f"mmdc error (code {result.returncode}): {result.stderr}")
                    # Try without config file as fallback
                    cmd_simple = self._mmdc_command([
                        "-i", mmd_path,
                        "-o", str(output_path),
                        "-b", background,
                    ])
                    result = subprocess.run(
                        cmd_simple,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if result.returncode != 0:
                        print(f"mmdc simple error: {result.stderr}")
                        self._create_placeholder(output_path, mermaid_code)
                        return True

                if output_path.exists():
                    print(f"Successfully rendered: {output_path}")
                    return True
                else:
                    print(f"Output file not created: {output_path}")
                    self._create_placeholder(output_path, mermaid_code)
                    return True

            finally:
                # Clean up temp files
                if os.path.exists(mmd_path):
                    os.unlink(mmd_path)
                if os.path.exists(config_path):
                    os.unlink(config_path)

        except subprocess.TimeoutExpired:
            print("mmdc timed out")
            self._create_placeholder(output_path, mermaid_code)
            return True

        except Exception as e:
            print(f"Error rendering mermaid: {e}")
            import traceback
            traceback.print_exc()
            self._create_placeholder(output_path, mermaid_code)
            return True

    def _create_placeholder(self, output_path: Path, mermaid_code: str) -> None:
        """
        Create a placeholder text file when rendering fails.

        The placeholder contains the mermaid code so users can
        render it manually using mermaid.live or similar tools.
        """
        placeholder_path = output_path.with_suffix('.mmd')

        # NOTE: mermaid uses '%%' for comments; a leading '#' is INVALID and makes
        # both mmdc and mermaid.live fail with "No diagram type detected", which
        # would also break this manual-render escape hatch.
        content = f"""%% Mermaid Diagram
%% Render this diagram at https://mermaid.live/

{mermaid_code}
"""

        with open(placeholder_path, 'w') as f:
            f.write(content)

        # Also create a simple text-based "image" placeholder
        try:
            # Try to create a minimal PNG placeholder if PIL is available
            from PIL import Image, ImageDraw, ImageFont

            img = Image.new('RGB', (800, 400), color='white')
            draw = ImageDraw.Draw(img)

            # Add text
            text = "Mermaid diagram placeholder\n\nSee .mmd file for diagram code\nor visit mermaid.live to render"
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
            except:
                font = ImageFont.load_default()

            # Center text
            draw.multiline_text(
                (400, 200),
                text,
                fill='black',
                font=font,
                anchor='mm',
                align='center',
            )

            img.save(output_path)

        except ImportError:
            # PIL not available, just save the mmd file
            pass

    def _extract_mermaid_blocks(
        self,
        content: str,
    ) -> List[Dict[str, Any]]:
        """
        Extract mermaid code blocks from markdown.

        Args:
            content: Markdown content.

        Returns:
            List of dicts with 'type' and 'code' keys.
        """
        mermaid_pattern = re.compile(
            r'```mermaid\s*\n(.*?)```',
            re.DOTALL
        )

        blocks = []
        for match in mermaid_pattern.finditer(content):
            code = match.group(1).strip()

            # Detect diagram type from first line
            first_line = code.split('\n')[0].strip().lower()
            diagram_type = self._detect_diagram_type(first_line)

            blocks.append({
                "type": diagram_type,
                "code": code,
            })

        return blocks

    def _detect_diagram_type(self, first_line: str) -> str:
        """Detect mermaid diagram type from first line."""
        first_line = first_line.lower()

        type_patterns = [
            (["graph", "flowchart"], "flowchart"),
            (["sequencediagram"], "sequence"),
            (["classdiagram"], "class"),
            (["statediagram"], "state"),
            (["erdiagram"], "er"),
            (["gantt"], "gantt"),
            (["pie"], "pie"),
            (["mindmap"], "mindmap"),
            (["timeline"], "timeline"),
            (["journey"], "journey"),
        ]

        for patterns, diagram_type in type_patterns:
            for pattern in patterns:
                if first_line.startswith(pattern):
                    return diagram_type

        return "unknown"

    def get_diagram_dimensions(self, diagram_type: str) -> Dict[str, int]:
        """Get recommended dimensions for a diagram type."""
        dimensions = {
            "flowchart": {"width": 1200, "height": 800},
            "sequence": {"width": 1000, "height": 1200},
            "class": {"width": 1400, "height": 1000},
            "state": {"width": 1000, "height": 800},
            "er": {"width": 1200, "height": 1000},
            "gantt": {"width": 1400, "height": 600},
            "pie": {"width": 800, "height": 600},
            "mindmap": {"width": 1200, "height": 800},
            "timeline": {"width": 1400, "height": 400},
            "journey": {"width": 1200, "height": 400},
        }
        return dimensions.get(diagram_type, {"width": 1200, "height": 800})


def render_mermaid_diagrams(
    company_slug: str,
    markdown_content: str,
    output_dir: Optional[Path] = None,
    diagram_names: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    Convenience function to render mermaid diagrams.

    Args:
        company_slug: URL-safe company name.
        markdown_content: Markdown content with mermaid blocks.
        output_dir: Optional output directory.
        diagram_names: Optional list of diagram names.

    Returns:
        Dict mapping diagram name to file path.
    """
    renderer = MermaidRenderer(output_dir=output_dir)
    return renderer.render_from_markdown(
        company_slug=company_slug,
        markdown_content=markdown_content,
        diagram_names=diagram_names,
    )
