"""DeepResearch service configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration values for the DeepResearch service."""

    SERVICE_NAME: str = "deep-research"
    SERVICE_VERSION: str = "0.1.0"
    HOST: str = "0.0.0.0"
    PORT: int = 8004

    RAG_SERVICE_URL: str = "http://scholarmind_api:8000"
    DATA_ROOT: str = "/app/data/deep_research"
    REQUEST_TIMEOUT: int = 120
    # Service-to-service auth (JWT)
    JWT_SECRET_KEY: str | None = None
    JWT_ACCESS_TOKEN_EXPIRE_DAYS: int = 30
    INTERNAL_SERVICE_ALLOWLIST: str = "scholarmind_api"

    MAX_ACTIVE_RUNS: int = 2
    MAX_PARALLEL_TOPICS: int = 3
    MAX_ITERATIONS: int = 7
    MAX_DEPTH: int = 2
    MAX_BREADTH: int = 8
    MAX_FOLLOWUPS_PER_BLOCK: int = 2
    FOLLOWUP_TRIGGER_MIN_CHARS: int = 200
    FOLLOWUP_EXECUTION_MODE: str = "queue"  # queue | inline
    FOLLOWUP_QUEUE_EXPANSION_ENABLED: bool = False
    FOLLOWUP_PLAN_MAX_ITEMS: int = 4
    AUTO_EXPAND_FROM_SUMMARY: bool = False
    MAX_FOLLOWUP_QUERIES_PER_BLOCK: int = 3
    MAX_CODE_EXEC_SNIPPETS: int = 2
    MAX_TOOL_CALLS_PER_BLOCK: int = 10
    AGENT_DECISION_MAX_ROUNDS: int = 3
    AGENT_MIN_EVIDENCE_QUALITY_SCORE: int = 60
    AGENT_FAIL_FAST_ON_TOOL_ERROR: bool = False
    AGENT_USE_FOLLOWUP_AS_SEARCH_QUERY: bool = False
    AGENT_ACTION_BEAM_WIDTH: int = 3
    AGENT_ENABLE_CODE_EXEC_AUTO: bool = True
    AGENT_ACADEMIC_PAPER_FIRST: bool = True

    ENABLE_WEB_SEARCH: bool = True
    WEB_SEARCH_PROVIDER: str = "tavily"
    WEB_SEARCH_API_KEY: str | None = None
    TAVILY_API_KEY: str | None = None
    SERPER_API_KEY: str | None = None
    WEB_SEARCH_BASE_URL: str = "https://api.tavily.com/search"
    WEB_SEARCH_MAX_RESULTS: int = 8
    WEB_SEARCH_INCLUDE_DOMAINS: str = ""
    WEB_SEARCH_EXCLUDE_DOMAINS: str = ""
    WEB_SEARCH_DOMAIN_ALLOWLIST: str = ""
    WEB_SEARCH_DOMAIN_DENYLIST: str = ""
    WEB_SEARCH_BLOCKED_TERMS: str = ""
    WEB_SEARCH_MIN_QUALITY_SCORE: float = 0.6
    WEB_SEARCH_MIN_QUERY_OVERLAP: float = 0.08
    WEB_SEARCH_TIMEOUT: int = 20
    ENABLE_CODE_EXEC: bool = True
    CODE_EXEC_TIMEOUT_SECONDS: int = 5
    CODE_EXEC_MAX_OUTPUT_CHARS: int = 2000
    CODE_EXEC_MAX_CODE_CHARS: int = 2000
    PAPER_SEARCH_MAX_RESULTS: int = 8
    PAPER_SEARCH_PROVIDERS: str = "semantic_scholar,arxiv"
    PAPER_SEARCH_RANK_BY: str = "hybrid"
    PAPER_SEARCH_MIN_PER_PROVIDER: int = 1
    PAPER_SEARCH_ARXIV_MAX_RESULTS: int = 8
    PAPER_SEARCH_ARXIV_MAX_AGE_YEARS: int = 5
    PAPER_SEARCH_ARXIV_TIMEOUT_SECONDS: int = 20
    PAPER_SEARCH_ARXIV_RETRIES: int = 2
    PAPER_SEARCH_ARXIV_DELAY_SECONDS: float = 0.5
    LOG_LEVEL: str = "INFO"
    AUTO_RECOVER_RUNS: bool = True
    PROGRESS_MAX_BYTES: int = 5_000_000
    PROGRESS_TAIL_LINES: int = 2000
    PROGRESS_TRIM_CHUNK_SIZE: int = 8192
    PROGRESS_META_THROTTLE_SECONDS: int = 2
    SESSION_INDEX_MAX_RUNS_PER_SESSION: int = 300

    RUN_TIMEOUT_SECONDS: int = 0
    RUN_IDLE_TIMEOUT_SECONDS: int = 0
    RUN_WATCHDOG_INTERVAL_SECONDS: int = 10

    QUEUE_BACKEND: str = "sqlite"  # sqlite | redis
    QUEUE_PRIORITY_AGING_SECONDS: int = 300
    QUEUE_MAX_PENDING: int = 200
    ENABLE_SYNC_RUN: bool = False

    SCHEDULER_LEASE_SECONDS: int = 30
    SCHEDULER_RENEW_SECONDS: int = 10

    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None
    REDIS_QUEUE_PREFIX: str = "deep_research:queue"

    # Report generation (LLM refinement)
    REPORT_LLM_ENABLED: bool = True
    REPORT_LLM_TEMPERATURE: float = 0.2
    REPORT_LLM_MAX_TOKENS: int = 2560
    # When enabled, generate the report section-by-section to surface progress events
    # and allow incremental report preview via snapshot/report.json.
    REPORT_LLM_SECTIONAL: bool = True
    REPORT_LLM_SECTION_MAX_TOKENS: int = 1280
    REPORT_LLM_SECTION_CONTEXT_MAX_CHARS: int = 6000
    REPORT_PROMPT_MAX_INPUT_TOKENS: int = 20000
    REPORT_SECTION_PROMPT_MAX_INPUT_TOKENS: int = 12000
    REPORT_SECTION_MAX_BLOCKS: int = 8
    REPORT_SECTION_MAX_NOTES_PER_BLOCK: int = 5
    REPORT_SECTION_MAX_NOTES_TOTAL: int = 36
    REPORT_SECTION_MAX_CITATIONS: int = 64
    REPORT_REFERENCES_MAX_TOTAL: int = 80
    REPORT_REFERENCES_MAX_PER_SECTION: int = 12
    REPORT_CITATION_DOMAIN_ALLOWLIST: str = ""
    REPORT_CITATION_DOMAIN_DENYLIST: str = ""
    REPORT_CITATION_BLOCKED_TERMS: str = ""
    REPORT_CITATION_MIN_QUALITY_SCORE: float = 0.9
    # General overlap gate for non-web, non-academic sources (e.g. RAG chunks).
    REPORT_CITATION_MIN_QUERY_OVERLAP: float = 0.06
    # Trusted academic domains (arxiv, semanticscholar, ieee …) bypass the overlap
    # gate entirely in code – their domain authority is the quality signal.
    # This setting is a documentation anchor; the bypass is applied in pipeline.py.
    REPORT_CITATION_ACADEMIC_MIN_QUERY_OVERLAP: float = 0.0
    # Web citations are noisier; require stricter topical overlap.
    REPORT_CITATION_WEB_MIN_QUERY_OVERLAP: float = 0.18
    # Relaxed fallback still enforces non-trivial topical relevance.
    REPORT_CITATION_RELAXED_MIN_QUERY_OVERLAP: float = 0.06
    REPORT_CITATION_RELAXED_MIN_QUALITY_SCORE: float = 0.55
    # Report quality gates (industrial guardrails against low-signal outputs).
    REPORT_MIN_COMPLETED_BLOCKS: int = 1
    REPORT_MIN_PARAGRAPHS_TOTAL: int = 6
    REPORT_MIN_CITATION_PARAGRAPH_COVERAGE: float = 0.2
    REPORT_MIN_DISTINCT_CITATIONS: int = 2
    RESEARCH_CONTEXT_MAX_CHARS: int = 6000
    # LLM provider preference for OpenAI-compatible clients
    PREFERRED_LLM_PROVIDER: str = "openai"  # dashscope | openai
    # Keep disabled by default to decouple DeepResearch quality from chat model selector.
    DEEP_RESEARCH_ALLOW_REQUEST_LLM_OVERRIDE: bool = False
    # Fail over to a backup provider/model list when primary endpoint fails.
    LLM_ENABLE_FAILOVER: bool = True
    LLM_FALLBACK_PROVIDER: str = "dashscope"  # dashscope | openai
    # DashScope (OpenAI-compatible)
    DASHSCOPE_API_KEY: str | None = None
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    DASHSCOPE_MODEL_NAME: str = "qwen3-max"
    DASHSCOPE_MODEL_CANDIDATES: str = "qwen3-max,qwen-max"
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL_NAME: str = "gpt-5.2"
    OPENAI_MODEL_CANDIDATES: str = "gpt-5.2,gpt-4.1,gpt-4o"
    LLM_POLICY_ENABLED: bool = True
    LLM_POLICY_VERSION: str = "v1"
    LLM_POLICY_MANIFEST_PATH: str = "/shared/llm_policy/llm_policy.v1.json"
    LLM_POLICY_TASK_RAG_SUMMARY: str = "deepresearch.rag_summary"
    LLM_POLICY_TASK_DECISION: str = "deepresearch.decision"
    LLM_POLICY_TASK_REPORT: str = "deepresearch.report"
    LLM_POLICY_TASK_REPORT_SECTION: str = "deepresearch.report_section"
    LLM_POLICY_AUDIT_ENABLED: bool = True
    LLM_PRICE_TABLE_JSON: str | None = None
    LLM_DEFAULT_INPUT_USD_PER_1K: float = 0.0
    LLM_DEFAULT_OUTPUT_USD_PER_1K: float = 0.0

    # Idea generation
    IDEAGEN_CONTEXT_MAX_CHARS: int = 8000
    IDEAGEN_MIN_KNOWLEDGE_POINTS: int = 3
    IDEAGEN_MAX_KNOWLEDGE_POINTS: int = 5
    IDEAGEN_MIN_IDEAS_PER_POINT: int = 5
    IDEAGEN_MAX_IDEAS_PER_POINT: int = 10
    IDEAGEN_NOTE_MAX_CHARS: int = 2000

    # Notebook notes
    NOTEBOOK_MAX_SELECTION_CHARS: int = 4000
    NOTEBOOK_SOURCE_EXCERPT_MAX_CHARS: int = 200
    NOTEBOOK_MAX_KEY_POINTS: int = 6
    NOTEBOOK_MAX_QUESTIONS: int = 4
    NOTEBOOK_TITLE_MAX_CHARS: int = 80

    # Research decision (tool selection & sufficiency)
    DECISION_LLM_ENABLED: bool = True
    DECISION_LLM_TEMPERATURE: float = 0.2
    DECISION_LLM_MAX_TOKENS: int = 768
    # Keep empty by default so model selection follows provider defaults.
    DECISION_LLM_MODEL_NAME: str = ""
    RESEARCH_MIN_SUMMARY_CHARS: int = 300
    RESEARCH_MIN_CITATIONS: int = 2
    MIN_DOCS_FOR_COMPARE: int = 2
    MAX_DOCS_FOR_COMPARE: int = 4
    COMPARE_DIMENSIONS_EN: str = "Methodology,Results,Limitations"
    COMPARE_DIMENSIONS_ZH: str = "方法,结果,局限性"
    # Strict runtime mode for development/debugging:
    # no silent fallback/degrade paths, fail fast on capability/config/runtime errors.
    STRICT_FAIL_FAST: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
