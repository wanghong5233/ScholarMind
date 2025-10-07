from typing import Set


# 简版白名单（示例，可后续从配置/数据库加载并扩充）
CCF_WHITELIST: Set[str] = {
    # CCF-A/B 常见会议/期刊（示例，不完全）
    "NeurIPS", "ICML", "ICLR", "CVPR", "ICCV", "ECCV",
    "AAAI", "IJCAI", "KDD", "SIGIR", "WWW", "ACL",
    "EMNLP", "NAACL", "COLING", "TPAMI", "JMLR",
    "ICDM", "CIKM", "WSDM", "SDM",
    "AAAI Conference on Artificial Intelligence",
}


def is_high_quality_venue(venue: str) -> bool:
    if not venue:
        return False
    v = venue.strip()
    return v in CCF_WHITELIST


