"""LLM-based report refinement."""

from typing import List, Optional

import logging

from service.llm_client import LLMClient
from service.report_templates import ReportTemplateBuilder


class ReportRefiner:
    """Refine report drafts using an LLM template."""

    def __init__(
        self,
        llm_client: LLMClient,
        language: str,
    ) -> None:
        """Initialize the report refiner.

        Args:
            llm_client (LLMClient): LLM client wrapper.
            language (str): Report language code.
        """

        self._llm_client = llm_client
        self._template_builder = ReportTemplateBuilder(language=language)
        self._logger = logging.getLogger("deep_research.report_refiner")

    async def refine(
        self,
        topic: str,
        outline: List[str],
        notes: List[str],
        citation_table: List[str],
        report_style: Optional[str] = None,
        context_text: Optional[str] = None,
    ) -> Optional[str]:
        """Generate a refined report with the LLM.

        Args:
            topic (str): Research topic.
            outline (List[str]): Outline items.
            notes (List[str]): Research notes.
            citation_table (List[str]): Reference table entries.
            report_style (Optional[str]): Style hint for the report.
            context_text (Optional[str]): Optional conversation context.

        Returns:
            Optional[str]: Refined report markdown, or None on failure.
        """

        prompt = self._template_builder.build_prompt(
            topic=topic,
            outline=outline,
            notes=notes,
            citation_table=citation_table,
            report_style=report_style,
            context_text=context_text,
        )
        output = await self._llm_client.generate(prompt)
        if not output:
            self._logger.warning("LLM report generation failed; using draft report.")
        return output
