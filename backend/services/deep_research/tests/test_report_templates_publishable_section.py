"""Tests for publishable-design report template constraints."""

from service.report_templates import ReportTemplateBuilder


def test_build_sections_contains_publishable_design_section() -> None:
    """Both zh/en templates should include publishable design section."""

    zh_sections = [section.title for section in ReportTemplateBuilder(language="zh").build_sections()]
    en_sections = [section.title for section in ReportTemplateBuilder(language="en").build_sections()]

    assert "可投稿问题定义与实验设计" in zh_sections
    assert "Publishable Problem Definition and Experiment Design" in en_sections


def test_build_prompt_includes_publishable_table_requirements() -> None:
    """Full report prompt should require publishable-table outputs."""

    builder = ReportTemplateBuilder(language="zh")
    prompt = builder.build_prompt(
        topic="边缘计算中的 GNN + DRL 任务卸载",
        outline=["- 方法路线", "- 实验评估"],
        notes=["对比基线包括启发式算法和 DRL 变体。"],
        citation_table=["Cite as [1]: Sample paper"],
    )

    assert "可投稿问题定义与实验设计" in prompt
    assert "候选可投稿问题定义" in prompt
    assert "实验设计矩阵" in prompt
    assert "[[编号]](#ref-编号)" in prompt
    assert "禁止使用占位符 [N]" in prompt


def test_build_section_prompt_enforces_publishable_table_structure() -> None:
    """Section prompt should enforce two mandatory markdown tables."""

    builder = ReportTemplateBuilder(language="en")
    prompt = builder.build_section_prompt(
        topic="GNN-DRL for edge offloading",
        section_title="Publishable Problem Definition and Experiment Design",
        section_guidance="Propose concrete experiments and publishable hypotheses.",
        outline=["- Problem framing", "- Evaluation matrix"],
        notes=["Need explicit novelty and reproducible experiment settings."],
        citation_table=["Cite as [1]: Example reference"],
    )

    assert "### Candidate Publishable Problems" in prompt
    assert "### Experiment Design Matrix" in prompt
    assert "Problem Definition | Academic Motivation/Gap | Method Novelty" in prompt
    assert "[[number]](#ref-number)" in prompt
    assert "Never use placeholders like [N]" in prompt
