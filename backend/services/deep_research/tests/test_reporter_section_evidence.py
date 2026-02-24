"""Tests for section-scoped report evidence retrieval."""

from agents.reporter_agent import ReporterAgent
from service.citation_manager import CitationManager
from service.data_structures import DynamicTopicQueue, ScholarCitation, TopicStatus


def test_section_evidence_pack_prioritizes_relevant_block() -> None:
    """Reporter should pick section-relevant blocks and citations."""

    queue = DynamicTopicQueue("research_test")
    root = queue.add_block(title="Edge AI", question="Edge AI", depth=0)
    queue.mark_block_status(root.block_id, TopicStatus.COMPLETED)

    block_method = queue.add_block(
        title="Method and benchmark design",
        question="What datasets and benchmarks are used?",
        depth=1,
        parent_id=root.block_id,
    )
    block_method.notes = [
        "Use benchmark datasets with ablation experiments to support evidence quality.",
        "Compare methods on latency and energy metrics.",
    ]
    queue.mark_block_status(block_method.block_id, TopicStatus.COMPLETED)

    block_background = queue.add_block(
        title="Background and definitions",
        question="What is edge intelligence?",
        depth=1,
        parent_id=root.block_id,
    )
    block_background.notes = [
        "Define core concepts and context.",
    ]
    queue.mark_block_status(block_background.block_id, TopicStatus.COMPLETED)

    manager = CitationManager("research_test")
    citation_method = ScholarCitation(
        citation_id="CIT-B001-01",
        title="Method Paper",
        url="https://example.com/method",
        snippet="Dataset and benchmark settings for evaluation.",
        source_type="paper.search",
    )
    citation_bg = ScholarCitation(
        citation_id="CIT-B002-01",
        title="Background Survey",
        url="https://example.com/background",
        snippet="Background definitions.",
        source_type="web.search",
    )
    manager.add_citation(citation_method)
    manager.add_citation(citation_bg)
    block_method.add_citation(citation_method.citation_id)
    block_background.add_citation(citation_bg.citation_id)
    manager.build_ref_map_for([citation_method.citation_id, citation_bg.citation_id])

    reporter = ReporterAgent(manager, language="en")
    evidence = reporter.build_section_evidence_pack(
        queue=queue,
        topic="Edge AI optimization",
        section_title="Methods and Evidence",
        section_guidance="Focus on datasets, benchmarks, and evidence reliability.",
        max_blocks=1,
        max_notes_per_block=3,
        max_total_notes=6,
        max_citations=4,
    )

    assert evidence.block_ids == [block_method.block_id]
    assert any("benchmark" in line.lower() for line in evidence.notes)
    assert any("Method Paper" in line for line in evidence.citation_table)
