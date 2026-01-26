"""Note agent for compressing research outputs."""

from typing import List

import re


class NoteAgent:
    """Compress raw summaries into short bullet notes."""

    def __init__(self, max_points: int = 3) -> None:
        """Initialize the note agent.

        Args:
            max_points (int): Maximum number of notes to return.
        """

        self._max_points = max(1, max_points)

    def compress(self, text: str) -> List[str]:
        """Convert free-form text into bullet notes.

        Args:
            text (str): Raw summary text.

        Returns:
            List[str]: Bullet notes derived from the summary.
        """

        if not text:
            return ["No notes generated for this topic."]

        bullet_lines = self._extract_bullets(text)
        if bullet_lines:
            return bullet_lines[: self._max_points]

        sentences = self._split_sentences(text)
        notes = [self._format_sentence(sentence) for sentence in sentences if sentence]
        return notes[: self._max_points] or ["No notes generated for this topic."]

    def _extract_bullets(self, text: str) -> List[str]:
        """Extract bullet-style lines from the text.

        Args:
            text (str): Raw input text.

        Returns:
            List[str]: Lines that already look like bullet points.
        """

        lines = []
        for line in (text or "").splitlines():
            cleaned = line.strip()
            if cleaned.startswith(("-", "*", "•")):
                lines.append(cleaned)
        return lines

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences.

        Args:
            text (str): Raw input text.

        Returns:
            List[str]: Sentence fragments.
        """

        chunks = re.split(r"(?<=[。！？.!?])\s+", text.strip())
        return [chunk.strip() for chunk in chunks if chunk.strip()]

    @staticmethod
    def _format_sentence(sentence: str) -> str:
        """Format a sentence as a bullet point.

        Args:
            sentence (str): Sentence to format.

        Returns:
            str: Bullet-formatted sentence.
        """

        return f"- {sentence}"
