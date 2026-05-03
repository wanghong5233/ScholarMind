"""Venue quality classification for online paper search.

Industrial-grade strategy:
- Maintain a curated catalog of venues (CCF official ranks + a JCR-CS subset).
- Each entry stores canonical name, common aliases and ISSN when available.
- The matcher accepts the full venue object that Semantic Scholar's
  ``publicationVenue`` returns (name + alternate_names + issn) so we can match
  on the most stable identifier first (ISSN), then aliases, then normalized
  canonical name. Pure regex on the visible name alone is fragile and produces
  many false negatives for journals whose printed title varies between sources.

Sources:
- CCF Conference & Journal Catalogue (2022 revision, public list).
- Web of Science / JCR Q1-Q2 in CS-related categories
  (Computer Networks & Communications, Hardware & Architecture, IoT-related),
  cross-checked with Clarivate's public quartile data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


# Strip noisy edition/format suffixes that Semantic Scholar appends to venue
# strings, e.g. "(Print)", "(Online)", "(Early Access)".
_EDITION_SUFFIX_RE = re.compile(
    r"\s*\((print|online|electronic|early access|in press)\)\s*$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class VenueRecord:
    name: str
    rank: str
    source: str  # "CCF" | "JCR"
    aliases: Tuple[str, ...] = ()
    issns: Tuple[str, ...] = ()
    type: Optional[str] = None  # "conference" | "journal"


# CCF 2022 catalog (curated subset, focuses on areas relevant to this codebase:
# AI/ML/NLP/CV, Systems, Networking, Security, DB, IR, HCI, SE, Theory).
# Full official list has ~620 venues; we cover the well-known ones plus the
# venues that show up on the user's IoT/communications testing path.
_CCF_RECORDS: Tuple[VenueRecord, ...] = (
    # ---------------- CCF-A (Artificial Intelligence) ----------------
    VenueRecord("AAAI Conference on Artificial Intelligence", "A", "CCF",
                aliases=("AAAI", "National Conference on Artificial Intelligence",
                         "AAAI Conf Artif Intell"),
                type="conference"),
    VenueRecord("Annual Meeting of the Association for Computational Linguistics", "A", "CCF",
                aliases=("ACL", "Annu Meet Assoc Comput Linguistics"),
                type="conference"),
    VenueRecord("Conference on Empirical Methods in Natural Language Processing", "A", "CCF",
                aliases=("EMNLP",), type="conference"),
    VenueRecord("Computer Vision and Pattern Recognition", "A", "CCF",
                aliases=("CVPR", "IEEE Conference on Computer Vision and Pattern Recognition",
                         "Conf Comput Vis Pattern Recognit"),
                type="conference"),
    VenueRecord("International Conference on Computer Vision", "A", "CCF",
                aliases=("ICCV", "IEEE International Conference on Computer Vision",
                         "Int Conf Comput Vis"),
                type="conference"),
    VenueRecord("European Conference on Computer Vision", "A", "CCF",
                aliases=("ECCV",), type="conference"),
    VenueRecord("International Conference on Machine Learning", "A", "CCF",
                aliases=("ICML",), type="conference"),
    VenueRecord("International Conference on Learning Representations", "A", "CCF",
                aliases=("ICLR",), type="conference"),
    VenueRecord("Conference on Neural Information Processing Systems", "A", "CCF",
                aliases=("NeurIPS", "NIPS",
                         "Advances in Neural Information Processing Systems",
                         "Adv Neural Inf Process Syst"),
                type="conference"),
    VenueRecord("International Joint Conference on Artificial Intelligence", "A", "CCF",
                aliases=("IJCAI",), type="conference"),
    VenueRecord("IEEE Transactions on Pattern Analysis and Machine Intelligence", "A", "CCF",
                aliases=("TPAMI", "IEEE Trans Pattern Anal Mach Intell"),
                issns=("0162-8828", "1939-3539"), type="journal"),
    VenueRecord("Journal of Machine Learning Research", "A", "CCF",
                aliases=("JMLR",), issns=("1532-4435", "1533-7928"), type="journal"),
    VenueRecord("Artificial Intelligence", "A", "CCF",
                aliases=("Artif Intell", "AIJ"), issns=("0004-3702",), type="journal"),
    # ---------------- CCF-A (Systems / Architecture / OS) ----------------
    VenueRecord("ACM Symposium on Operating Systems Principles", "A", "CCF",
                aliases=("SOSP",), type="conference"),
    VenueRecord("USENIX Symposium on Operating Systems Design and Implementation", "A", "CCF",
                aliases=("OSDI",), type="conference"),
    VenueRecord("USENIX Annual Technical Conference", "A", "CCF",
                aliases=("USENIX ATC", "ATC"), type="conference"),
    VenueRecord("International Symposium on Computer Architecture", "A", "CCF",
                aliases=("ISCA",), type="conference"),
    VenueRecord("International Symposium on Microarchitecture", "A", "CCF",
                aliases=("MICRO", "IEEE/ACM International Symposium on Microarchitecture"),
                type="conference"),
    VenueRecord("International Symposium on High-Performance Computer Architecture", "A", "CCF",
                aliases=("HPCA",), type="conference"),
    VenueRecord("ACM SIGPLAN Conference on Programming Language Design and Implementation", "A", "CCF",
                aliases=("PLDI",), type="conference"),
    VenueRecord("IEEE Transactions on Computers", "A", "CCF",
                aliases=("TC", "IEEE Trans Comput"),
                issns=("0018-9340", "1557-9956"), type="journal"),
    # ---------------- CCF-A (Networking) ----------------
    VenueRecord("ACM SIGCOMM Conference", "A", "CCF",
                aliases=("SIGCOMM",), type="conference"),
    VenueRecord("USENIX Symposium on Networked Systems Design and Implementation", "A", "CCF",
                aliases=("NSDI",), type="conference"),
    VenueRecord("IEEE Conference on Computer Communications", "A", "CCF",
                aliases=("INFOCOM",), type="conference"),
    VenueRecord("ACM Annual International Conference on Mobile Computing and Networking", "A", "CCF",
                aliases=("MobiCom",), type="conference"),
    VenueRecord("IEEE/ACM Transactions on Networking", "A", "CCF",
                aliases=("ToN", "TON"), issns=("1063-6692", "1558-2566"), type="journal"),
    # ---------------- CCF-A (Security) ----------------
    VenueRecord("ACM Conference on Computer and Communications Security", "A", "CCF",
                aliases=("CCS",), type="conference"),
    VenueRecord("USENIX Security Symposium", "A", "CCF",
                aliases=("USENIX Security",), type="conference"),
    VenueRecord("IEEE Symposium on Security and Privacy", "A", "CCF",
                aliases=("S&P", "IEEE S&P", "Oakland"), type="conference"),
    VenueRecord("Network and Distributed System Security Symposium", "A", "CCF",
                aliases=("NDSS",), type="conference"),
    # ---------------- CCF-A (Database / Data Mining / IR) ----------------
    VenueRecord("ACM SIGMOD International Conference on Management of Data", "A", "CCF",
                aliases=("SIGMOD",), type="conference"),
    VenueRecord("International Conference on Very Large Data Bases", "A", "CCF",
                aliases=("VLDB", "PVLDB", "Proc VLDB Endow"), type="conference"),
    VenueRecord("ACM SIGKDD Conference on Knowledge Discovery and Data Mining", "A", "CCF",
                aliases=("KDD",), type="conference"),
    VenueRecord("ACM SIGIR Conference on Research and Development in Information Retrieval", "A", "CCF",
                aliases=("SIGIR",), type="conference"),
    VenueRecord("The Web Conference", "A", "CCF",
                aliases=("WWW", "International World Wide Web Conference"), type="conference"),
    VenueRecord("ACM Transactions on Database Systems", "A", "CCF",
                aliases=("TODS",), issns=("0362-5915",), type="journal"),
    VenueRecord("IEEE Transactions on Knowledge and Data Engineering", "A", "CCF",
                aliases=("TKDE", "IEEE Trans Knowl Data Eng"),
                issns=("1041-4347", "1558-2191"), type="journal"),
    # ---------------- CCF-A (SE / Theory) ----------------
    VenueRecord("International Conference on Software Engineering", "A", "CCF",
                aliases=("ICSE",), type="conference"),
    VenueRecord("IEEE Transactions on Software Engineering", "A", "CCF",
                aliases=("TSE",), issns=("0098-5589", "1939-3520"), type="journal"),
    VenueRecord("Symposium on Theory of Computing", "A", "CCF",
                aliases=("STOC",), type="conference"),
    VenueRecord("Symposium on Foundations of Computer Science", "A", "CCF",
                aliases=("FOCS",), type="conference"),
    # ---------------- CCF-B (AI / NLP / CV) ----------------
    VenueRecord("North American Chapter of the Association for Computational Linguistics", "B", "CCF",
                aliases=("NAACL",), type="conference"),
    VenueRecord("International Conference on Computational Linguistics", "B", "CCF",
                aliases=("COLING",), type="conference"),
    VenueRecord("European Conference on Artificial Intelligence", "B", "CCF",
                aliases=("ECAI",), type="conference"),
    VenueRecord("International Conference on Multimedia", "B", "CCF",
                aliases=("ACM MM", "MM"), type="conference"),
    VenueRecord("Pattern Recognition", "B", "CCF",
                aliases=("Pattern Recognit", "PR"),
                issns=("0031-3203",), type="journal"),
    # ---------------- CCF-B (Systems / Networks) ----------------
    VenueRecord("IEEE International Conference on Distributed Computing Systems", "B", "CCF",
                aliases=("ICDCS",), type="conference"),
    VenueRecord("ACM/IEEE International Conference on Mobile Systems, Applications, and Services", "B", "CCF",
                aliases=("MobiSys",), type="conference"),
    VenueRecord("ACM Conference on Embedded Networked Sensor Systems", "B", "CCF",
                aliases=("SenSys",), type="conference"),
    VenueRecord("ACM/IFIP International Conference on Distributed Systems Platforms (Middleware)", "B", "CCF",
                aliases=("Middleware",), type="conference"),
    VenueRecord("IEEE/IFIP International Conference on Dependable Systems and Networks", "B", "CCF",
                aliases=("DSN",), type="conference"),
    VenueRecord("IEEE Transactions on Mobile Computing", "B", "CCF",
                aliases=("TMC", "IEEE Trans Mob Comput"),
                issns=("1536-1233", "1558-0660"), type="journal"),
    VenueRecord("IEEE Transactions on Parallel and Distributed Systems", "B", "CCF",
                aliases=("TPDS", "IEEE Trans Parallel Distrib Syst"),
                issns=("1045-9219", "1558-2183"), type="journal"),
    VenueRecord("IEEE Transactions on Wireless Communications", "B", "CCF",
                aliases=("TWC",), issns=("1536-1276", "1558-2248"), type="journal"),
    VenueRecord("Computer Networks", "B", "CCF",
                aliases=("Comput Netw",), issns=("1389-1286",), type="journal"),
    VenueRecord("ACM Transactions on Sensor Networks", "B", "CCF",
                aliases=("TOSN",), issns=("1550-4859",), type="journal"),
    VenueRecord("ACM Transactions on Internet Technology", "B", "CCF",
                aliases=("TOIT",), issns=("1533-5399",), type="journal"),
    # ---------------- CCF-B (DB / DM / IR) ----------------
    VenueRecord("IEEE International Conference on Data Engineering", "B", "CCF",
                aliases=("ICDE",), type="conference"),
    VenueRecord("ACM International Conference on Information and Knowledge Management", "B", "CCF",
                aliases=("CIKM",), type="conference"),
    VenueRecord("ACM International Conference on Web Search and Data Mining", "B", "CCF",
                aliases=("WSDM",), type="conference"),
    VenueRecord("IEEE International Conference on Data Mining", "B", "CCF",
                aliases=("ICDM",), type="conference"),
    VenueRecord("SIAM International Conference on Data Mining", "B", "CCF",
                aliases=("SDM",), type="conference"),
    VenueRecord("European Conference on Information Retrieval", "B", "CCF",
                aliases=("ECIR",), type="conference"),
    VenueRecord("ACM Transactions on Information Systems", "B", "CCF",
                aliases=("TOIS",), issns=("1046-8188",), type="journal"),
    VenueRecord("ACM Transactions on Intelligent Systems and Technology", "B", "CCF",
                aliases=("TIST",), issns=("2157-6904",), type="journal"),
    # ---------------- CCF-A (Surveys / Security / SE journals) ----------------
    VenueRecord("ACM Computing Surveys", "A", "CCF",
                aliases=("CSUR", "ACM Comput Surv"), issns=("0360-0300",), type="journal"),
    VenueRecord("IEEE Transactions on Dependable and Secure Computing", "A", "CCF",
                aliases=("TDSC",), issns=("1545-5971", "1941-0018"), type="journal"),
    VenueRecord("Communications of the ACM", "A", "CCF",
                aliases=("CACM",), issns=("0001-0782",), type="journal"),
    VenueRecord("ACM Transactions on Software Engineering and Methodology", "A", "CCF",
                aliases=("TOSEM",), issns=("1049-331X",), type="journal"),
    # ---------------- CCF-B (Surveys / Communications) ----------------
    VenueRecord("IEEE Communications Surveys and Tutorials", "B", "CCF",
                aliases=("COMST", "IEEE Commun Surv Tutorials"),
                issns=("1553-877X",), type="journal"),
    VenueRecord("IEEE Communications Magazine", "B", "CCF",
                aliases=("Commun Mag",), issns=("0163-6804",), type="journal"),
    VenueRecord("IEEE Transactions on Intelligent Transportation Systems", "B", "CCF",
                aliases=("T-ITS", "IEEE Trans Intell Transp Syst"),
                issns=("1524-9050",), type="journal"),
    VenueRecord("Neurocomputing", "C", "CCF",
                issns=("0925-2312",), type="journal"),
    # ---------------- CCF-C (Networking / Communications - dense in user's domain) ----------------
    VenueRecord("IEEE International Conference on Communications", "C", "CCF",
                aliases=("ICC",), type="conference"),
    VenueRecord("IEEE Global Communications Conference", "C", "CCF",
                aliases=("GLOBECOM", "IEEE GLOBECOM",
                         "Global Communications Conference",
                         "Glob Commun Conf"),
                type="conference"),
    VenueRecord("IEEE Wireless Communications and Networking Conference", "C", "CCF",
                aliases=("WCNC",), type="conference"),
    VenueRecord("IEEE Local Computer Networks Conference", "C", "CCF",
                aliases=("LCN",), type="conference"),
    VenueRecord("International Conference on Mobility, Sensing and Networking", "C", "CCF",
                aliases=("MSN",), type="conference"),
    VenueRecord("IEEE International Conference on High Performance Computing and Communications", "C", "CCF",
                aliases=("HPCC",), type="conference"),
    VenueRecord("International Conference on Algorithms and Architectures for Parallel Processing", "C", "CCF",
                aliases=("ICA3PP",), type="conference"),
    VenueRecord("IEEE International Parallel and Distributed Processing Symposium", "C", "CCF",
                aliases=("IPDPS",), type="conference"),
    VenueRecord("Telecommunications Systems", "C", "CCF",
                aliases=("Telecommun Syst",), issns=("1018-4864",), type="journal"),
    VenueRecord("China Communications", "C", "CCF",
                aliases=("China Commun",), issns=("1673-5447",), type="journal"),
)


# JCR-CS subset: well-known journals with current quartile in CS-related categories.
# This list is intentionally small and explicit; missing entries simply degrade
# gracefully to "unranked" instead of pretending.
_JCR_RECORDS: Tuple[VenueRecord, ...] = (
    VenueRecord("IEEE Internet of Things Journal", "Q1", "JCR",
                aliases=("IEEE Internet Thing J",),
                issns=("2327-4662",), type="journal"),
    VenueRecord("IEEE Transactions on Industrial Informatics", "Q1", "JCR",
                aliases=("TII",), issns=("1551-3203",), type="journal"),
    VenueRecord("IEEE Transactions on Cognitive Communications and Networking", "Q1", "JCR",
                aliases=("TCCN",), issns=("2332-7731",), type="journal"),
    VenueRecord("IEEE Transactions on Network and Service Management", "Q1", "JCR",
                aliases=("TNSM",), issns=("1932-4537",), type="journal"),
    VenueRecord("IEEE Transactions on Network Science and Engineering", "Q1", "JCR",
                aliases=("TNSE",), issns=("2327-4697",), type="journal"),
    VenueRecord("IEEE Transactions on Cloud Computing", "Q1", "JCR",
                aliases=("TCC",), issns=("2168-7161",), type="journal"),
    VenueRecord("IEEE Transactions on Services Computing", "Q1", "JCR",
                aliases=("TSC",), issns=("1939-1374",), type="journal"),
    VenueRecord("IEEE Transactions on Geoscience and Remote Sensing", "Q1", "JCR",
                aliases=("TGRS", "IEEE Trans Geosci Remote Sens"),
                issns=("0196-2892",), type="journal"),
    VenueRecord("IEEE Transactions on Communications", "Q1", "JCR",
                aliases=("TCOM",), issns=("0090-6778",), type="journal"),
    VenueRecord("IEEE Transactions on Vehicular Technology", "Q2", "JCR",
                aliases=("TVT",), issns=("0018-9545",), type="journal"),
    VenueRecord("Future Generation Computer Systems", "Q1", "JCR",
                aliases=("FGCS",), issns=("0167-739X",), type="journal"),
    VenueRecord("Journal of Network and Computer Applications", "Q1", "JCR",
                aliases=("JNCA",), issns=("1084-8045",), type="journal"),
    VenueRecord("Computer Communications", "Q2", "JCR",
                aliases=("Comput Commun",), issns=("0140-3664",), type="journal"),
    VenueRecord("International Journal of Intelligent Systems", "Q1", "JCR",
                aliases=("Int J Intell Syst",), issns=("0884-8173",), type="journal"),
    VenueRecord("Expert Systems with Applications", "Q1", "JCR",
                aliases=("Expert Syst Appl",), issns=("0957-4174",), type="journal"),
    VenueRecord("IEEE Access", "Q2", "JCR",
                issns=("2169-3536",), type="journal"),
    VenueRecord("Sensors", "Q2", "JCR",
                aliases=("MDPI Sensors",), issns=("1424-8220",), type="journal"),
    VenueRecord("Electronics", "Q3", "JCR",
                aliases=("MDPI Electronics",), issns=("2079-9292",), type="journal"),
    VenueRecord("Scientific Reports", "Q1", "JCR",
                aliases=("Sci Rep",), issns=("2045-2322",), type="journal"),
    # CCF-A/B journals that are also high-quartile in JCR. Listing them in
    # both catalogs lets the UI show "CCF-A | JCR-Q1" side-by-side, which is
    # the actual reality for most top-tier CS journals.
    VenueRecord("IEEE Transactions on Pattern Analysis and Machine Intelligence", "Q1", "JCR",
                aliases=("TPAMI", "IEEE Trans Pattern Anal Mach Intell"),
                issns=("0162-8828", "1939-3539"), type="journal"),
    VenueRecord("IEEE Transactions on Knowledge and Data Engineering", "Q1", "JCR",
                aliases=("TKDE", "IEEE Trans Knowl Data Eng"),
                issns=("1041-4347", "1558-2191"), type="journal"),
    VenueRecord("IEEE Transactions on Mobile Computing", "Q1", "JCR",
                aliases=("TMC", "IEEE Trans Mob Comput"),
                issns=("1536-1233", "1558-0660"), type="journal"),
    VenueRecord("IEEE Transactions on Parallel and Distributed Systems", "Q1", "JCR",
                aliases=("TPDS", "IEEE Trans Parallel Distrib Syst"),
                issns=("1045-9219", "1558-2183"), type="journal"),
    VenueRecord("IEEE Transactions on Computers", "Q1", "JCR",
                aliases=("IEEE Trans Comput",), issns=("0018-9340", "1557-9956"), type="journal"),
    VenueRecord("IEEE/ACM Transactions on Networking", "Q1", "JCR",
                aliases=("ToN", "TON"), issns=("1063-6692", "1558-2566"), type="journal"),
    VenueRecord("IEEE Transactions on Software Engineering", "Q1", "JCR",
                aliases=("TSE",), issns=("0098-5589", "1939-3520"), type="journal"),
    VenueRecord("IEEE Transactions on Dependable and Secure Computing", "Q1", "JCR",
                aliases=("TDSC",), issns=("1545-5971", "1941-0018"), type="journal"),
    VenueRecord("IEEE Communications Surveys and Tutorials", "Q1", "JCR",
                aliases=("COMST", "IEEE Commun Surv Tutorials"),
                issns=("1553-877X",), type="journal"),
    VenueRecord("ACM Computing Surveys", "Q1", "JCR",
                aliases=("CSUR", "ACM Comput Surv"), issns=("0360-0300",), type="journal"),
    VenueRecord("Journal of Machine Learning Research", "Q1", "JCR",
                aliases=("JMLR",), issns=("1532-4435", "1533-7928"), type="journal"),
    VenueRecord("Artificial Intelligence", "Q1", "JCR",
                aliases=("Artif Intell", "AIJ"), issns=("0004-3702",), type="journal"),
    VenueRecord("Pattern Recognition", "Q1", "JCR",
                aliases=("Pattern Recognit",), issns=("0031-3203",), type="journal"),
    VenueRecord("Neurocomputing", "Q2", "JCR",
                issns=("0925-2312",), type="journal"),
)


# Explicit rank ordering. Industry consensus among CS researchers in China:
#   - CCF-A is the unambiguous top tier.
#   - CCF-B is roughly on par with JCR-Q1 (top 25% within a JCR category).
#   - CCF-C is roughly on par with JCR-Q2; CCF-C does NOT outrank JCR-Q1.
#   - JCR-Q3 and Q4 are below CCF-C.
# Using a fixed enumeration (no fabricated "scores") keeps comparisons
# explainable: ``RANK_ORDER[label]`` is the only knob that matters.
RANK_ORDER: Dict[str, int] = {
    "CCF-A":  7,
    "CCF-B":  6,
    "JCR-Q1": 6,
    "CCF-C":  5,
    "JCR-Q2": 5,
    "JCR-Q3": 3,
    "JCR-Q4": 2,
}


def _normalize(value: str) -> str:
    if not value:
        return ""
    cleaned = _EDITION_SUFFIX_RE.sub("", value).strip().lower().replace("&", "and")
    return " ".join(cleaned.split())


def _build_index(
    records: Iterable[VenueRecord],
) -> Tuple[Dict[str, VenueRecord], Dict[str, VenueRecord]]:
    by_name: Dict[str, VenueRecord] = {}
    by_issn: Dict[str, VenueRecord] = {}
    for record in records:
        keys = [record.name, *record.aliases]
        for key in keys:
            normalized = _normalize(key)
            if normalized and normalized not in by_name:
                by_name[normalized] = record
        for issn in record.issns:
            issn_key = (issn or "").strip().lower()
            if issn_key and issn_key not in by_issn:
                by_issn[issn_key] = record
    return by_name, by_issn


_CCF_BY_NAME, _CCF_BY_ISSN = _build_index(_CCF_RECORDS)
_JCR_BY_NAME, _JCR_BY_ISSN = _build_index(_JCR_RECORDS)


def _lookup(
    *,
    venue_name: Optional[str],
    alternate_names: Optional[List[str]],
    issn: Optional[str],
    by_name: Dict[str, VenueRecord],
    by_issn: Dict[str, VenueRecord],
) -> Optional[VenueRecord]:
    """Match by ISSN first, then by exact normalized name (canonical + aliases).

    Substring matching is intentionally NOT used: venue names overlap
    pathologically (e.g. ``Sensors`` (MDPI journal) vs.
    ``Italian National Conference on Sensors``; ``Artificial Intelligence``
    (Elsevier) vs. ``IEEE Transactions on Artificial Intelligence``), which
    produces silent false positives. Semantic Scholar already returns the
    canonical name and an ``alternate_names`` list per venue, so we rely on
    that structured data plus a curated catalog for matching.
    """

    if issn:
        record = by_issn.get(issn.strip().lower())
        if record:
            return record

    candidates: List[str] = []
    if venue_name:
        candidates.append(venue_name)
    if alternate_names:
        candidates.extend(alternate_names)

    for candidate in candidates:
        normalized = _normalize(candidate)
        if not normalized:
            continue
        if normalized in by_name:
            return by_name[normalized]
    return None


def classify_venue_quality(
    venue_name: Optional[str],
    *,
    alternate_names: Optional[List[str]] = None,
    issn: Optional[str] = None,
) -> Dict[str, Any]:
    """Classify a venue against both CCF and JCR catalogs.

    Returns BOTH labels when a venue is present in both systems (very common,
    e.g. TPAMI is CCF-A *and* JCR-Q1). The previous "CCF first, JCR fallback"
    behaviour silently dropped the JCR tag for any CCF-listed venue, which
    threw away useful context for the UI.

    Args:
        venue_name: Visible venue name from the upstream API.
        alternate_names: Alias list (e.g. Semantic Scholar's
            ``publicationVenue.alternate_names``); used when the visible name is
            an abbreviation or non-standard variant.
        issn: ISSN from the upstream API; preferred matching key when present.

    Returns:
        Dict with:
          - ``labels``: list of all matched ``{source, rank, label}`` (may be empty)
          - ``source``/``rank``/``label``: the *primary* (highest-ranked) match,
            kept for backward compatibility
          - ``score``: ordinal of the primary label (higher = better,
            see ``RANK_ORDER``); 0 when nothing matched
    """

    ccf = _lookup(
        venue_name=venue_name,
        alternate_names=alternate_names,
        issn=issn,
        by_name=_CCF_BY_NAME,
        by_issn=_CCF_BY_ISSN,
    )
    jcr = _lookup(
        venue_name=venue_name,
        alternate_names=alternate_names,
        issn=issn,
        by_name=_JCR_BY_NAME,
        by_issn=_JCR_BY_ISSN,
    )

    labels: List[Dict[str, str]] = []
    if ccf:
        labels.append({"source": "CCF", "rank": ccf.rank, "label": f"CCF-{ccf.rank}"})
    if jcr:
        labels.append({"source": "JCR", "rank": jcr.rank, "label": f"JCR-{jcr.rank}"})

    if not labels:
        return {
            "source": None,
            "rank": None,
            "label": None,
            "score": 0,
            "labels": [],
        }

    primary = max(labels, key=lambda item: RANK_ORDER.get(item["label"], 0))
    return {
        "source": primary["source"],
        "rank": primary["rank"],
        "label": primary["label"],
        "score": RANK_ORDER.get(primary["label"], 0),
        "labels": labels,
    }


def is_high_quality_venue(venue: str) -> bool:
    """Backward-compatible helper retained for existing call-sites."""
    quality = classify_venue_quality(venue)
    return quality["source"] == "CCF" and quality["rank"] in {"A", "B"}
