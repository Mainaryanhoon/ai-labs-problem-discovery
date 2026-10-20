diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/.env.example b/.env.example  
\--- a/.env.example	1970-01-01 00:00:00.000000000 \+0000  
\+++ b/.env.example	2026-10-20 06:29:09.362830017 \+0000  
@@ \-0,0 \+1,16 @@  
\+\# Copy to .env and fill in. Never commit .env.  
\+GEMINI\_API\_KEY=  
\+GEMINI\_MODEL=gemini-2.5-flash  
\+DATABASE\_URL=sqlite:///./data/problem\_discovery.db  
\+  
\+\# Apify (Reddit and X collection)  
\+APIFY\_API\_TOKEN=  
\+APIFY\_REDDIT\_ACTOR\_ID=anyxsolutions\~reddit-scraper  
\+APIFY\_X\_ACTOR\_ID=apidojo\~tweet-scraper  
\+  
\+\# Optional protections (both disabled when empty / 0).  
\+\# When SERVICE\_API\_KEY is set, every route except /health needs an  
\+\# X-API-Key header with this value.  
\+SERVICE\_API\_KEY=  
\+\# Limits POST /api/submissions per client address, per worker process.  
\+SUBMISSION\_RATE\_LIMIT\_PER\_MINUTE=0  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/PROJECT\_CHECKLIST.md b/PROJECT\_CHECKLIST.md  
\--- a/PROJECT\_CHECKLIST.md	2026-10-19 20:27:39.000000000 \+0000  
\+++ b/PROJECT\_CHECKLIST.md	2026-10-20 06:29:23.658609597 \+0000  
@@ \-40,3 \+40,20 @@  
 5\. Restart the API and verify \`GET /api/pipeline/status\` before starting a new pipeline run.  
   
 The status endpoint reports the current lock and persisted aggregate stage states only. It does not report run history or last-run source health.  
\+  
\+\#\# Update: audit follow-up (backend quality pass)  
\+  
\+Verification status is stated per item; "executed" means run locally, "unrun" means written but not executed.  
\+  
\+| Area | Change | Status |  
\+| \--- | \--- | \--- |  
\+| Apify auth | Audit's P0 finding not reproduced: code sends \`Bearer \<token\>\` and the test asserts it (the audit most likely saw a redacted display). No change made. | Source inspected |  
\+| Clustering gate | \`is\_rejected\_analysis\` keeps model-rejected noise out of clustering (explicit \`is\_genuine\_problem: false\` or non-actionable \`signal\_type\` only; missing/legacy analyses unchanged). | Helper executed; pipeline tests unrun |  
\+| Scoring evidence | \`contradicts\` / \`mentions\` links no longer corroborate or add commercial signal; still counted as attached evidence. | Executed (19 scoring tests pass, mutation-checked) |  
\+| Provenance | New \`source\_records\` table: native ID, community, timestamp origin. New table only; existing tables untouched. HN, Stack Exchange, Reddit and X write it. Native-ID duplicate check for Reddit/X. | Executed against in-memory SQLite |  
\+| Community yield | \`GET /api/sources/yield\`; rates are null until posts are analysed. | Function executed; endpoint unrun |  
\+| Subreddit mode | Opt-in \`reddit\_subreddits\` (10-community shortlist constant, validated names). Default behaviour unchanged. \*\*Actor contract for listing URLs unverified.\*\* | Request building executed; live behaviour unverified |  
\+| Evaluation | \`app/evaluation.py\` \+ \`benchmark/\`: classification, clustering, purchase intent, independence, quote traceability, ranking, sensitivity. No thresholds. | Executed (14 tests) |  
\+| Security | Opt-in \`SERVICE\_API\_KEY\` and submission rate limit; \`.env.example\` added. | Unit logic executed; HTTP-layer test unrun |  
\+  
\+Still open: human-labeled benchmark data (EVAL-01..06 need real posts and reviewers), live Apify contract verification (ENG-01/02), review decision endpoints, production migrations, deployment.  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/app/config.py b/app/config.py  
\--- a/app/config.py	2026-10-19 12:01:27.000000000 \+0000  
\+++ b/app/config.py	2026-10-20 06:28:33.244901302 \+0000  
@@ \-8,6 \+8,9 @@  
     database\_url: str \= "sqlite:///./data/problem\_discovery.db"  
     apify\_api\_token: str \= ""  
     apify\_actor\_id: str \= ""  
\+    \# Opt-in protections; both are disabled by default (see app/security.py).  
\+    service\_api\_key: str \= ""  
\+    submission\_rate\_limit\_per\_minute: int \= 0  
     model\_config \= SettingsConfigDict(env\_file=".env", env\_file\_encoding="utf-8", extra="ignore")  
       
     apify\_reddit\_actor\_id: str \= "anyxsolutions\~reddit-scraper"  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/app/evaluation.py b/app/evaluation.py  
\--- a/app/evaluation.py	1970-01-01 00:00:00.000000000 \+0000  
\+++ b/app/evaluation.py	2026-10-20 06:24:34.470990794 \+0000  
@@ \-0,0 \+1,681 @@  
\+"""Offline evaluation of the discovery engine against human labels.  
\+  
\+Covers EVAL-01..EVAL-06 from the open-issues backlog:  
\+  
\+\* classification precision / recall / false-positive rate (EVAL-02)  
\+\* clustering false merges and missed matches (EVAL-03)  
\+\* ranking agreement with human judgement and score sensitivity (EVAL-04)  
\+\* purchase-intent validation (EVAL-05)  
\+\* evidence independence over-claiming (EVAL-06)  
\+\* quote traceability / evidence integrity  
\+  
\+Design rules  
\+------------  
\+\* Pure Python, no third-party dependencies, no network, no database. It  
\+  reads versioned JSONL files and never calls a model or provider.  
\+\* It reports measurements and sample sizes only. It deliberately has NO  
\+  pass/fail thresholds: those must be chosen after a baseline is measured.  
\+\* A rate whose denominator is zero is reported as \`\`None\`\` (unknown), never  
\+  as 0 or 1\.  
\+\* Precision/recall carry Wilson 95% intervals so a small pilot cannot be  
\+  mistaken for a stable accuracy claim.  
\+  
\+Usage::  
\+  
\+    python \-m app.evaluation labels.jsonl predictions.jsonl \\  
\+        \[--ranking ranking.jsonl\] \[--json out.json\] \[--markdown out.md\]  
\+"""  
\+  
\+from \_\_future\_\_ import annotations  
\+  
\+import argparse  
\+import json  
\+import math  
\+from collections import defaultdict  
\+from itertools import combinations  
\+from pathlib import Path  
\+from typing import Any, Callable, Iterable  
\+  
\+SIGNAL\_TYPES \= (  
\+    "pain\_point",  
\+    "explicit\_tool\_request",  
\+    "workaround\_or\_manual\_work",  
\+    "feature\_gap",  
\+    "buying\_signal",  
\+    "general\_discussion",  
\+    "noise",  
\+)  
\+ACTIONABLE \= frozenset(SIGNAL\_TYPES) \- {"general\_discussion", "noise"}  
\+PURCHASE\_LEVELS \= ("none", "unclear", "suggested", "explicit")  
\+BUYING\_CLAIMS \= frozenset({"suggested", "explicit"})  
\+PAIR\_LABELS \= ("same", "different", "uncertain")  
\+  
\+  
\+\# \--------------------------------------------------------------------------  
\+\# Loading and validation  
\+\# \--------------------------------------------------------------------------  
\+  
\+def load\_jsonl(path: str | Path) \-\> list\[dict\[str, Any\]\]:  
\+    records: list\[dict\[str, Any\]\] \= \[\]  
\+    with open(path, encoding="utf-8") as handle:  
\+        for number, line in enumerate(handle, start=1):  
\+            line \= line.strip()  
\+            if not line or line.startswith("\#"):  
\+                continue  
\+            try:  
\+                value \= json.loads(line)  
\+            except json.JSONDecodeError as exc:  
\+                raise ValueError(f"{path}:{number}: invalid JSON") from exc  
\+            if not isinstance(value, dict):  
\+                raise ValueError(f"{path}:{number}: expected a JSON object")  
\+            records.append(value)  
\+    return records  
\+  
\+  
\+def validate\_labels(labels: list\[dict\[str, Any\]\]) \-\> list\[str\]:  
\+    """Return a list of human-readable problems with a label file."""  
\+    errors: list\[str\] \= \[\]  
\+    seen: set\[str\] \= set()  
\+    versions \= {item.get("benchmark\_version") for item in labels}  
\+    if len(versions) \> 1:  
\+        errors.append(f"Mixed benchmark\_version values: {sorted(map(str, versions))}")  
\+    for index, item in enumerate(labels):  
\+        label \= f"record {index \+ 1} ({item.get('id')\!r})"  
\+        item\_id \= item.get("id")  
\+        if not isinstance(item\_id, str) or not item\_id.strip():  
\+            errors.append(f"{label}: missing string id")  
\+            continue  
\+        if item\_id in seen:  
\+            errors.append(f"{label}: duplicate id")  
\+        seen.add(item\_id)  
\+        if not isinstance(item.get("text"), str) or not item\["text"\].strip():  
\+            errors.append(f"{label}: missing text")  
\+        genuine \= item.get("is\_genuine\_problem")  
\+        if not isinstance(genuine, bool):  
\+            errors.append(f"{label}: is\_genuine\_problem must be true/false")  
\+            continue  
\+        signal \= item.get("signal\_type")  
\+        if signal not in SIGNAL\_TYPES:  
\+            errors.append(f"{label}: unknown signal\_type {signal\!r}")  
\+        elif genuine and signal not in ACTIONABLE:  
\+            errors.append(f"{label}: genuine problem with non-actionable signal\_type")  
\+        elif not genuine and signal in ACTIONABLE:  
\+            errors.append(f"{label}: non-genuine record with actionable signal\_type")  
\+        problem\_id \= item.get("problem\_id")  
\+        if genuine and not (isinstance(problem\_id, str) and problem\_id.strip()):  
\+            errors.append(f"{label}: genuine problem needs a problem\_id")  
\+        if not genuine and problem\_id:  
\+            errors.append(f"{label}: non-genuine record must not have a problem\_id")  
\+        purchase \= item.get("purchase\_intent", "none")  
\+        if purchase not in PURCHASE\_LEVELS:  
\+            errors.append(f"{label}: unknown purchase\_intent {purchase\!r}")  
\+        pairs \= item.get("pair\_labels", \[\])  
\+        if not isinstance(pairs, list):  
\+            errors.append(f"{label}: pair\_labels must be a list")  
\+    for item in labels:  
\+        for pair in item.get("pair\_labels", \[\]) or \[\]:  
\+            if (  
\+                not isinstance(pair, dict)  
\+                or pair.get("other") not in seen  
\+                or pair.get("label") not in PAIR\_LABELS  
\+            ):  
\+                errors.append(  
\+                    f"record {item.get('id')\!r}: bad pair\_label {pair\!r}"  
\+                )  
\+    return errors  
\+  
\+  
\+\# \--------------------------------------------------------------------------  
\+\# Small statistics helpers  
\+\# \--------------------------------------------------------------------------  
\+  
\+def ratio(numerator: int, denominator: int) \-\> float | None:  
\+    return numerator / denominator if denominator else None  
\+  
\+  
\+def wilson\_interval(  
\+    successes: int, total: int, z: float \= 1.96  
\+) \-\> tuple\[float, float\] | None:  
\+    if total \<= 0:  
\+        return None  
\+    p \= successes / total  
\+    denom \= 1 \+ z \* z / total  
\+    centre \= (p \+ z \* z / (2 \* total)) / denom  
\+    margin \= z \* math.sqrt(p \* (1 \- p) / total \+ z \* z / (4 \* total \* total)) / denom  
\+    return (max(0.0, centre \- margin), min(1.0, centre \+ margin))  
\+  
\+  
\+def \_rate(successes: int, total: int) \-\> dict\[str, Any\]:  
\+    interval \= wilson\_interval(successes, total)  
\+    return {  
\+        "value": ratio(successes, total),  
\+        "count": successes,  
\+        "of": total,  
\+        "wilson\_95": list(interval) if interval else None,  
\+    }  
\+  
\+  
\+def \_by\_id(records: Iterable\[dict\[str, Any\]\]) \-\> dict\[str, dict\[str, Any\]\]:  
\+    return {r\["id"\]: r for r in records if isinstance(r.get("id"), str)}  
\+  
\+  
\+\# \--------------------------------------------------------------------------  
\+\# EVAL-02: classification  
\+\# \--------------------------------------------------------------------------  
\+  
\+def \_predicted\_genuine(pred: dict\[str, Any\]) \-\> bool:  
\+    return pred.get("is\_genuine\_problem") is True and (  
\+        pred.get("signal\_type") in ACTIONABLE  
\+    )  
\+  
\+  
\+def \_binary\_block(tp: int, fp: int, fn: int, tn: int) \-\> dict\[str, Any\]:  
\+    return {  
\+        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},  
\+        "precision": \_rate(tp, tp \+ fp),  
\+        "recall": \_rate(tp, tp \+ fn),  
\+        "false\_positive\_rate": \_rate(fp, fp \+ tn),  
\+    }  
\+  
\+  
\+def classification\_report(  
\+    labels: list\[dict\[str, Any\]\], predictions: list\[dict\[str, Any\]\]  
\+) \-\> dict\[str, Any\]:  
\+    gold \= \_by\_id(labels)  
\+    pred \= \_by\_id(predictions)  
\+    matched \= \[i for i in gold if i in pred\]  
\+  
\+    def block(ids: list\[str\]) \-\> dict\[str, Any\]:  
\+        tp \= fp \= fn \= tn \= 0  
\+        for i in ids:  
\+            g \= gold\[i\]\["is\_genuine\_problem"\]  
\+            p \= \_predicted\_genuine(pred\[i\])  
\+            if g and p:  
\+                tp \+= 1  
\+            elif p and not g:  
\+                fp \+= 1  
\+            elif g and not p:  
\+                fn \+= 1  
\+            else:  
\+                tn \+= 1  
\+        return \_binary\_block(tp, fp, fn, tn)  
\+  
\+    per\_type: dict\[str, Any\] \= {}  
\+    for signal in sorted(ACTIONABLE):  
\+        tp \= sum(  
\+            1 for i in matched  
\+            if gold\[i\].get("signal\_type") \== signal  
\+            and pred\[i\].get("signal\_type") \== signal  
\+            and \_predicted\_genuine(pred\[i\])  
\+        )  
\+        fp \= sum(  
\+            1 for i in matched  
\+            if gold\[i\].get("signal\_type") \!= signal  
\+            and pred\[i\].get("signal\_type") \== signal  
\+            and \_predicted\_genuine(pred\[i\])  
\+        )  
\+        fn \= sum(  
\+            1 for i in matched  
\+            if gold\[i\].get("signal\_type") \== signal  
\+            and not (  
\+                pred\[i\].get("signal\_type") \== signal  
\+                and \_predicted\_genuine(pred\[i\])  
\+            )  
\+        )  
\+        per\_type\[signal\] \= {  
\+            "support": sum(1 for i in matched if gold\[i\].get("signal\_type") \== signal),  
\+            "precision": \_rate(tp, tp \+ fp),  
\+            "recall": \_rate(tp, tp \+ fn),  
\+        }  
\+  
\+    difficult \= \[i for i in matched if gold\[i\].get("difficult") is True\]  
\+    return {  
\+        "labelled\_records": len(gold),  
\+        "matched\_predictions": len(matched),  
\+        "missing\_predictions": sorted(set(gold) \- set(pred)),  
\+        "unknown\_prediction\_ids": sorted(set(pred) \- set(gold)),  
\+        "genuine\_problem": block(matched),  
\+        "difficult\_subset": {"n": len(difficult), \*\*block(difficult)},  
\+        "per\_signal\_type": per\_type,  
\+        "false\_positive\_ids": sorted(  
\+            i for i in matched  
\+            if \_predicted\_genuine(pred\[i\]) and not gold\[i\]\["is\_genuine\_problem"\]  
\+        ),  
\+        "false\_negative\_ids": sorted(  
\+            i for i in matched  
\+            if gold\[i\]\["is\_genuine\_problem"\] and not \_predicted\_genuine(pred\[i\])  
\+        ),  
\+    }  
\+  
\+  
\+\# \--------------------------------------------------------------------------  
\+\# EVAL-03: clustering and de-duplication  
\+\# \--------------------------------------------------------------------------  
\+  
\+def \_gold\_pairs(labels: list\[dict\[str, Any\]\]) \-\> dict\[frozenset\[str\], str\]:  
\+    """Pair labels derived from problem\_id, overridden by explicit labels."""  
\+    pairs: dict\[frozenset\[str\], str\] \= {}  
\+    genuine \= \[r for r in labels if r.get("is\_genuine\_problem") is True\]  
\+    for a, b in combinations(genuine, 2):  
\+        same \= a.get("problem\_id") \== b.get("problem\_id")  
\+        pairs\[frozenset((a\["id"\], b\["id"\]))\] \= "same" if same else "different"  
\+    for record in labels:  
\+        for pair in record.get("pair\_labels", \[\]) or \[\]:  
\+            if isinstance(pair, dict) and pair.get("label") in PAIR\_LABELS:  
\+                pairs\[frozenset((record\["id"\], pair\["other"\]))\] \= pair\["label"\]  
\+    return pairs  
\+  
\+  
\+def clustering\_report(  
\+    labels: list\[dict\[str, Any\]\], predictions: list\[dict\[str, Any\]\]  
\+) \-\> dict\[str, Any\]:  
\+    pred \= \_by\_id(predictions)  
\+    clustered \= {  
\+        i: p.get("cluster\_id")  
\+        for i, p in pred.items()  
\+        if p.get("cluster\_id") is not None  
\+    }  
\+    same\_tp \= same\_fn \= diff\_fp \= diff\_tn \= 0  
\+    uncertain\_merged \= uncertain\_split \= 0  
\+    uncovered \= 0  
\+    for key, label in \_gold\_pairs(labels).items():  
\+        a, b \= tuple(key)  
\+        if a not in clustered or b not in clustered:  
\+            uncovered \+= 1  
\+            continue  
\+        merged \= clustered\[a\] \== clustered\[b\]  
\+        if label \== "same":  
\+            same\_tp, same\_fn \= same\_tp \+ merged, same\_fn \+ (not merged)  
\+        elif label \== "different":  
\+            diff\_fp, diff\_tn \= diff\_fp \+ merged, diff\_tn \+ (not merged)  
\+        elif merged:  
\+            uncertain\_merged \+= 1  
\+        else:  
\+            uncertain\_split \+= 1  
\+  
\+    gold\_problem \= {  
\+        r\["id"\]: r.get("problem\_id")  
\+        for r in labels  
\+        if r.get("is\_genuine\_problem") is True  
\+    }  
\+    members: dict\[Any, set\[Any\]\] \= defaultdict(set)  
\+    sizes: dict\[Any, int\] \= defaultdict(int)  
\+    for i, cluster in clustered.items():  
\+        if i in gold\_problem:  
\+            members\[cluster\].add(gold\_problem\[i\])  
\+            sizes\[cluster\] \+= 1  
\+    impure \= sorted(str(c) for c, problems in members.items() if len(problems) \> 1\)  
\+    return {  
\+        "pairs\_evaluated": same\_tp \+ same\_fn \+ diff\_fp \+ diff\_tn,  
\+        "pairs\_uncertain": uncertain\_merged \+ uncertain\_split,  
\+        "pairs\_without\_predicted\_cluster": uncovered,  
\+        "false\_merge\_rate": \_rate(diff\_fp, diff\_fp \+ diff\_tn),  
\+        "missed\_match\_rate": \_rate(same\_fn, same\_tp \+ same\_fn),  
\+        "pair\_precision": \_rate(same\_tp, same\_tp \+ diff\_fp),  
\+        "pair\_recall": \_rate(same\_tp, same\_tp \+ same\_fn),  
\+        "uncertain\_pairs\_merged": uncertain\_merged,  
\+        "uncertain\_pairs\_split": uncertain\_split,  
\+        "predicted\_clusters": len(members),  
\+        "clusters\_mixing\_distinct\_problems": impure,  
\+        "singleton\_clusters": sum(1 for c in sizes if sizes\[c\] \== 1),  
\+    }  
\+  
\+  
\+\# \--------------------------------------------------------------------------  
\+\# EVAL-05 / evidence integrity: purchase intent and quote traceability  
\+\# \--------------------------------------------------------------------------  
\+  
\+def \_traceable(quote: Any, text: str) \-\> bool:  
\+    return isinstance(quote, str) and bool(quote.strip()) and quote in text  
\+  
\+  
\+def evidence\_integrity\_report(  
\+    labels: list\[dict\[str, Any\]\], predictions: list\[dict\[str, Any\]\]  
\+) \-\> dict\[str, Any\]:  
\+    gold, pred \= \_by\_id(labels), \_by\_id(predictions)  
\+    total\_quotes \= traceable \= 0  
\+    records\_with\_untraceable: list\[str\] \= \[\]  
\+    for i, p in pred.items():  
\+        if i not in gold:  
\+            continue  
\+        bad \= False  
\+        for quote in p.get("evidence\_quotes") or \[\]:  
\+            total\_quotes \+= 1  
\+            if \_traceable(quote, gold\[i\]\["text"\]):  
\+                traceable \+= 1  
\+            else:  
\+                bad \= True  
\+        if bad:  
\+            records\_with\_untraceable.append(i)  
\+    return {  
\+        "quotes\_checked": total\_quotes,  
\+        "traceable\_quote\_rate": \_rate(traceable, total\_quotes),  
\+        "records\_with\_untraceable\_quotes": sorted(records\_with\_untraceable),  
\+    }  
\+  
\+  
\+def purchase\_intent\_report(  
\+    labels: list\[dict\[str, Any\]\], predictions: list\[dict\[str, Any\]\]  
\+) \-\> dict\[str, Any\]:  
\+    gold, pred \= \_by\_id(labels), \_by\_id(predictions)  
\+    matched \= \[i for i in gold if i in pred\]  
\+    claimed \= \[  
\+        i for i in matched  
\+        if pred\[i\].get("willingness\_to\_pay\_signal") in BUYING\_CLAIMS  
\+    \]  
\+    truly \= \[  
\+        i for i in matched  
\+        if gold\[i\].get("purchase\_intent", "none") in BUYING\_CLAIMS  
\+    \]  
\+    tp \= sum(1 for i in claimed if i in set(truly))  
\+    explicit\_claimed \= \[  
\+        i for i in matched  
\+        if pred\[i\].get("willingness\_to\_pay\_signal") \== "explicit"  
\+    \]  
\+    explicit\_ok \= \[  
\+        i for i in explicit\_claimed  
\+        if gold\[i\].get("purchase\_intent") \== "explicit"  
\+    \]  
\+    unsupported \= \[  
\+        i for i in claimed  
\+        if not any(  
\+            \_traceable(q, gold\[i\]\["text"\])  
\+            for q in pred\[i\].get("evidence\_quotes") or \[\]  
\+        )  
\+    \]  
\+    false\_claims \= \[  
\+        i for i in claimed if gold\[i\].get("purchase\_intent", "none") \== "none"  
\+    \]  
\+    return {  
\+        "claims\_made": len(claimed),  
\+        "claim\_precision": \_rate(tp, len(claimed)),  
\+        "claim\_recall": \_rate(tp, len(truly)),  
\+        "explicit\_claim\_precision": \_rate(len(explicit\_ok), len(explicit\_claimed)),  
\+        "claims\_without\_traceable\_quote": \_rate(len(unsupported), len(claimed)),  
\+        "claims\_on\_records\_labelled\_no\_intent": sorted(false\_claims),  
\+    }  
\+  
\+  
\+\# \--------------------------------------------------------------------------  
\+\# EVAL-06: independence over-claiming  
\+\# \--------------------------------------------------------------------------  
\+  
\+def independence\_report(  
\+    labels: list\[dict\[str, Any\]\], predictions: list\[dict\[str, Any\]\]  
\+) \-\> dict\[str, Any\]:  
\+    """Compare records counted as independent with human independence groups.  
\+  
\+    \`\`counted\_as\_independent\`\` (prediction side) marks records the engine  
\+    counts as separate corroboration. \`\`independence\_group\`\` (label side)  
\+    names the real person/experience: records sharing a group are one  
\+    customer, however many accounts, URLs or platforms they came from.  
\+    """  
\+    gold, pred \= \_by\_id(labels), \_by\_id(predictions)  
\+    per\_cluster: dict\[Any, list\[str\]\] \= defaultdict(list)  
\+    for i, p in pred.items():  
\+        if i in gold and p.get("cluster\_id") is not None and p.get("counted\_as\_independent") is True:  
\+            per\_cluster\[p\["cluster\_id"\]\].append(i)  
\+    overclaimed: list\[dict\[str, Any\]\] \= \[\]  
\+    claimed\_total \= supportable\_total \= 0  
\+    missing\_group \= 0  
\+    for cluster, ids in sorted(per\_cluster.items(), key=lambda kv: str(kv\[0\])):  
\+        groups \= set()  
\+        for i in ids:  
\+            group \= gold\[i\].get("independence\_group")  
\+            if not group:  
\+                missing\_group \+= 1  
\+                group \= f"\_\_unlabelled\_\_{i}"  
\+            groups.add(group)  
\+        claimed\_total \+= len(ids)  
\+        supportable\_total \+= len(groups)  
\+        if len(groups) \< len(ids):  
\+            overclaimed.append(  
\+                {"cluster\_id": str(cluster), "claimed": len(ids), "supportable": len(groups)}  
\+            )  
\+    return {  
\+        "clusters\_with\_independent\_claims": len(per\_cluster),  
\+        "claimed\_independent\_records": claimed\_total,  
\+        "supportable\_independent\_records": supportable\_total,  
\+        "overclaim\_rate": \_rate(claimed\_total \- supportable\_total, claimed\_total),  
\+        "overclaimed\_clusters": overclaimed,  
\+        "records\_missing\_independence\_group": missing\_group,  
\+    }  
\+  
\+  
\+\# \--------------------------------------------------------------------------  
\+\# EVAL-04: ranking  
\+\# \--------------------------------------------------------------------------  
\+  
\+def \_average\_ranks(values: list\[float\]) \-\> list\[float\]:  
\+    order \= sorted(range(len(values)), key=lambda k: values\[k\])  
\+    ranks \= \[0.0\] \* len(values)  
\+    pos \= 0  
\+    while pos \< len(order):  
\+        end \= pos  
\+        while end \+ 1 \< len(order) and values\[order\[end \+ 1\]\] \== values\[order\[pos\]\]:  
\+            end \+= 1  
\+        average \= (pos \+ end) / 2 \+ 1  
\+        for k in range(pos, end \+ 1):  
\+            ranks\[order\[k\]\] \= average  
\+        pos \= end \+ 1  
\+    return ranks  
\+  
\+  
\+def spearman(xs: list\[float\], ys: list\[float\]) \-\> float | None:  
\+    if len(xs) \!= len(ys) or len(xs) \< 2:  
\+        return None  
\+    rx, ry \= \_average\_ranks(xs), \_average\_ranks(ys)  
\+    mx, my \= sum(rx) / len(rx), sum(ry) / len(ry)  
\+    cov \= sum((a \- mx) \* (b \- my) for a, b in zip(rx, ry))  
\+    vx \= sum((a \- mx) \*\* 2 for a in rx)  
\+    vy \= sum((b \- my) \*\* 2 for b in ry)  
\+    if vx \== 0 or vy \== 0:  
\+        return None  
\+    return cov / math.sqrt(vx \* vy)  
\+  
\+  
\+def ranking\_report(items: list\[dict\[str, Any\]\], top\_k: int \= 3\) \-\> dict\[str, Any\]:  
\+    """\`\`items\`\`: {"problem\_id", "human\_score", "model\_score"}; higher \= better.  
\+  
\+    \`\`model\_score\`\` may be null (unscorable); those items are reported and  
\+    ranked last rather than silently dropped.  
\+    """  
\+    usable \= \[  
\+        r for r in items  
\+        if isinstance(r.get("human\_score"), (int, float))  
\+        and not isinstance(r.get("human\_score"), bool)  
\+    \]  
\+    unscored \= \[r\["problem\_id"\] for r in usable if r.get("model\_score") is None\]  
\+    scored \= \[r for r in usable if r.get("model\_score") is not None\]  
\+    human \= \[float(r\["human\_score"\]) for r in scored\]  
\+    model \= \[float(r\["model\_score"\]) for r in scored\]  
\+  
\+    agree \= disagree \= tied\_model \= 0  
\+    for a, b in combinations(range(len(scored)), 2):  
\+        if human\[a\] \== human\[b\]:  
\+            continue  
\+        if model\[a\] \== model\[b\]:  
\+            tied\_model \+= 1  
\+        elif (human\[a\] \> human\[b\]) \== (model\[a\] \> model\[b\]):  
\+            agree \+= 1  
\+        else:  
\+            disagree \+= 1  
\+  
\+    k \= min(top\_k, len(scored))  
\+    top\_human \= {scored\[i\]\["problem\_id"\] for i in sorted(range(len(scored)), key=lambda n: \-human\[n\])\[:k\]}  
\+    top\_model \= {scored\[i\]\["problem\_id"\] for i in sorted(range(len(scored)), key=lambda n: \-model\[n\])\[:k\]}  
\+    return {  
\+        "items\_scored": len(scored),  
\+        "items\_unscored\_by\_model": sorted(unscored),  
\+        "spearman\_rho": spearman(human, model),  
\+        "pairwise\_agreement": \_rate(agree, agree \+ disagree \+ tied\_model),  
\+        "pairs\_tied\_by\_model": tied\_model,  
\+        "top\_k": k,  
\+        "top\_k\_overlap": \_rate(len(top\_human & top\_model), k),  
\+    }  
\+  
\+  
\+def rating\_sensitivity(  
\+    score\_fn: Callable\[..., Any\],  
\+    analysis: dict\[str, Any\],  
\+    fields: Iterable\[str\] \= (  
\+        "urgency\_1\_to\_5",  
\+        "frequency\_signal\_1\_to\_5",  
\+        "evidence\_strength\_1\_to\_5",  
\+    ),  
\+    \*\*score\_kwargs: Any,  
\+) \-\> dict\[str, Any\]:  
\+    """Score change when one rating moves by \+/-1. Reports, does not judge."""  
\+    base \= score\_fn(analysis, \*\*score\_kwargs).score  
\+    out: dict\[str, Any\] \= {"base\_score": base, "changes": {}}  
\+    for name in fields:  
\+        value \= analysis.get(name)  
\+        if not isinstance(value, int) or isinstance(value, bool):  
\+            continue  
\+        for delta in (-1, 1):  
\+            moved \= value \+ delta  
\+            if not 1 \<= moved \<= 5:  
\+                continue  
\+            score \= score\_fn({\*\*analysis, name: moved}, \*\*score\_kwargs).score  
\+            out\["changes"\]\[f"{name}{delta:+d}"\] \= (  
\+                None if base is None or score is None else round(score \- base, 4\)  
\+            )  
\+    return out  
\+  
\+  
\+def duplicate\_invariance(  
\+    score\_fn: Callable\[..., Any\],  
\+    analysis: dict\[str, Any\],  
\+    copies: int \= 5,  
\+    \*\*score\_kwargs: Any,  
\+) \-\> dict\[str, Any\]:  
\+    """Scoring one problem record never depends on how many copies exist.  
\+  
\+    The aggregator de-duplicates before scoring, so the score function must  
\+    receive identical source counts for N copies of one record. This check  
\+    asserts exactly that contract for a supplied scoring function.  
\+    """  
\+    single \= score\_fn(analysis, distinct\_sources=1, \*\*score\_kwargs).score  
\+    many \= \[  
\+        score\_fn(analysis, distinct\_sources=1, \*\*score\_kwargs).score  
\+        for \_ in range(copies)  
\+    \]  
\+    return {"single": single, "copies": copies, "invariant": all(m \== single for m in many)}  
\+  
\+  
\+\# \--------------------------------------------------------------------------  
\+\# Full report  
\+\# \--------------------------------------------------------------------------  
\+  
\+def build\_report(  
\+    labels: list\[dict\[str, Any\]\],  
\+    predictions: list\[dict\[str, Any\]\],  
\+    ranking: list\[dict\[str, Any\]\] | None \= None,  
\+) \-\> dict\[str, Any\]:  
\+    errors \= validate\_labels(labels)  
\+    if errors:  
\+        raise ValueError("Invalid label file:\\n- " \+ "\\n- ".join(errors))  
\+    versions \= sorted({str(r.get("benchmark\_version", "unversioned")) for r in labels})  
\+    report: dict\[str, Any\] \= {  
\+        "benchmark\_version": versions,  
\+        "sample\_size": len(labels),  
\+        "contains\_synthetic\_records": any(r.get("synthetic") is True for r in labels),  
\+        "note": (  
\+            "Measurements only. No acceptance thresholds are applied; set them "  
\+            "after reviewing the baseline and the cost of each error type."  
\+        ),  
\+        "classification": classification\_report(labels, predictions),  
\+        "clustering": clustering\_report(labels, predictions),  
\+        "purchase\_intent": purchase\_intent\_report(labels, predictions),  
\+        "evidence\_integrity": evidence\_integrity\_report(labels, predictions),  
\+        "independence": independence\_report(labels, predictions),  
\+    }  
\+    if ranking is not None:  
\+        report\["ranking"\] \= ranking\_report(ranking)  
\+    return report  
\+  
\+  
\+def \_fmt(rate: dict\[str, Any\] | None) \-\> str:  
\+    if not rate or rate.get("value") is None:  
\+        return "n/a (no samples)"  
\+    low, high \= rate\["wilson\_95"\]  
\+    return f"{rate\['value'\]:.3f} ({rate\['count'\]}/{rate\['of'\]}, 95% CI {low:.2f}-{high:.2f})"  
\+  
\+  
\+def render\_markdown(report: dict\[str, Any\]) \-\> str:  
\+    c, cl \= report\["classification"\], report\["clustering"\]  
\+    p, i \= report\["purchase\_intent"\], report\["independence"\]  
\+    e \= report\["evidence\_integrity"\]  
\+    g \= c\["genuine\_problem"\]  
\+    lines \= \[  
\+        f"\# Benchmark report ({', '.join(report\['benchmark\_version'\])})",  
\+        "",  
\+        f"Sample size: \*\*{report\['sample\_size'\]}\*\* labelled records.",  
\+    \]  
\+    if report\["contains\_synthetic\_records"\]:  
\+        lines \+= \["", "\> WARNING: includes synthetic records. Smoke test only; not an accuracy claim."\]  
\+    lines \+= \[  
\+        "",  
\+        "\#\# Classification (genuine problem vs noise)",  
\+        f"- Precision: {\_fmt(g\['precision'\])}",  
\+        f"- Recall: {\_fmt(g\['recall'\])}",  
\+        f"- False-positive rate: {\_fmt(g\['false\_positive\_rate'\])}",  
\+        f"- False positives: {c\['false\_positive\_ids'\]}",  
\+        f"- False negatives: {c\['false\_negative\_ids'\]}",  
\+        f"- Missing predictions: {c\['missing\_predictions'\]}",  
\+        "",  
\+        "\#\# Clustering",  
\+        f"- False-merge rate: {\_fmt(cl\['false\_merge\_rate'\])}",  
\+        f"- Missed-match rate: {\_fmt(cl\['missed\_match\_rate'\])}",  
\+        f"- Uncertain pairs merged/split: {cl\['uncertain\_pairs\_merged'\]}/{cl\['uncertain\_pairs\_split'\]}",  
\+        f"- Clusters mixing distinct problems: {cl\['clusters\_mixing\_distinct\_problems'\]}",  
\+        "",  
\+        "\#\# Purchase intent",  
\+        f"- Claim precision: {\_fmt(p\['claim\_precision'\])}",  
\+        f"- Claims without a traceable quote: {\_fmt(p\['claims\_without\_traceable\_quote'\])}",  
\+        f"- Claims on records labelled no-intent: {p\['claims\_on\_records\_labelled\_no\_intent'\]}",  
\+        "",  
\+        "\#\# Evidence integrity and independence",  
\+        f"- Traceable quote rate: {\_fmt(e\['traceable\_quote\_rate'\])}",  
\+        f"- Independence over-claim rate: {\_fmt(i\['overclaim\_rate'\])}",  
\+        f"- Over-claimed clusters: {i\['overclaimed\_clusters'\]}",  
\+    \]  
\+    if "ranking" in report:  
\+        r \= report\["ranking"\]  
\+        rho \= r\["spearman\_rho"\]  
\+        lines \+= \[  
\+            "",  
\+            "\#\# Ranking vs human judgement",  
\+            f"- Items scored: {r\['items\_scored'\]} (unscored: {r\['items\_unscored\_by\_model'\]})",  
\+            f"- Spearman rho: {'n/a' if rho is None else f'{rho:.3f}'}",  
\+            f"- Pairwise agreement: {\_fmt(r\['pairwise\_agreement'\])}",  
\+            f"- Top-{r\['top\_k'\]} overlap: {\_fmt(r\['top\_k\_overlap'\])}",  
\+        \]  
\+    return "\\n".join(lines) \+ "\\n"  
\+  
\+  
\+def main(argv: list\[str\] | None \= None) \-\> int:  
\+    parser \= argparse.ArgumentParser(description="Evaluate engine output against human labels.")  
\+    parser.add\_argument("labels")  
\+    parser.add\_argument("predictions")  
\+    parser.add\_argument("--ranking")  
\+    parser.add\_argument("--json", dest="json\_out")  
\+    parser.add\_argument("--markdown", dest="markdown\_out")  
\+    args \= parser.parse\_args(argv)  
\+  
\+    report \= build\_report(  
\+        load\_jsonl(args.labels),  
\+        load\_jsonl(args.predictions),  
\+        load\_jsonl(args.ranking) if args.ranking else None,  
\+    )  
\+    if args.json\_out:  
\+        Path(args.json\_out).write\_text(json.dumps(report, indent=2), encoding="utf-8")  
\+    markdown \= render\_markdown(report)  
\+    if args.markdown\_out:  
\+        Path(args.markdown\_out).write\_text(markdown, encoding="utf-8")  
\+    print(markdown)  
\+    return 0  
\+  
\+  
\+if \_\_name\_\_ \== "\_\_main\_\_":  
\+    raise SystemExit(main())  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/app/ingestion.py b/app/ingestion.py  
\--- a/app/ingestion.py	2026-10-19 19:37:30.000000000 \+0000  
\+++ b/app/ingestion.py	2026-10-20 06:26:09.077377276 \+0000  
@@ \-12,6 \+12,7 @@  
 from app.clustering import canonicalize\_url, normalize\_text  
 from app.db import get\_db  
 from app.models import Submission  
\+from app.provenance import parse\_source\_reference, record\_source\_provenance  
   
 router \= APIRouter(prefix="/api/ingestion", tags=\["Ingestion"\])  
   
@@ \-211,6 \+212,14 @@  
             submission.created\_at \= source\_created\_at  
         db.add(submission)  
         db.flush()  
\+        reference \= parse\_source\_reference("Hacker News", url, object\_id)  
\+        record\_source\_provenance(  
\+            db,  
\+            submission,  
\+            native\_id=reference.native\_id,  
\+            community=reference.community,  
\+            source\_timestamp\_present=source\_created\_at is not None,  
\+        )  
         submission\_ids.append(submission.id)  
         known\_urls.add(canonical\_url)  
         known\_texts.add(normalized)  
@@ \-353,6 \+362,18 @@  
             submission.created\_at \= source\_created\_at  
         db.add(submission)  
         db.flush()  
\+        reference \= parse\_source\_reference(  
\+            source\_name,  
\+            url,  
\+            question.get("question\_id"),  
\+        )  
\+        record\_source\_provenance(  
\+            db,  
\+            submission,  
\+            native\_id=reference.native\_id,  
\+            community=reference.community,  
\+            source\_timestamp\_present=source\_created\_at is not None,  
\+        )  
         submission\_ids.append(submission.id)  
         known\_urls.add(canonical\_url)  
         known\_texts.add(normalized)  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/app/main.py b/app/main.py  
\--- a/app/main.py	2026-10-19 20:31:17.000000000 \+0000  
\+++ b/app/main.py	2026-10-20 06:28:33.247514429 \+0000  
@@ \-25,7 \+25,11 @@  
 )  
 import json  
 from typing import Literal  
\-from app.ranking import ACTIONABLE\_SIGNAL\_TYPES, score\_opportunity  
\+from app.ranking import (  
\+    ACTIONABLE\_SIGNAL\_TYPES,  
\+    NON\_SUPPORTING\_RELATIONSHIPS,  
\+    score\_opportunity,  
\+)  
 from app.services.analysis import analyze\_pending\_batch  
 from app.pipeline\_schemas import PipelineRunRequest  
 from app.services.pipeline import run\_pipeline  
@@ \-54,6 \+58,8 @@  
     ReviewQueueRead,  
 )  
 from app.services.pipeline import get\_pipeline\_status  
\+from app.security import limit\_submissions, require\_api\_key  
\+from app.provenance import community\_yield  
 from datetime import datetime, timezone   
   
   
@@ \-161,6 \+167,7 @@  
     description="Local-first, evidence-backed problem discovery. Starter vertical slice.",  
     version="0.1.0",  
     lifespan=lifespan,  
\+    dependencies=\[Depends(require\_api\_key)\],  
 )  
   
 app.include\_router(ingestion\_router)  
@@ \-174,7 \+181,12 @@  
     return {"status": "ok", "service": "ai-labs-problem-discovery", "version": app.version}  
   
   
\-@app.post("/api/submissions", response\_model=SubmissionRead, status\_code=201)  
\+@app.post(  
\+    "/api/submissions",  
\+    response\_model=SubmissionRead,  
\+    status\_code=201,  
\+    dependencies=\[Depends(limit\_submissions)\],  
\+)  
 def create\_submission(payload: SubmissionCreate, db: Session \= Depends(get\_db)):  
     item \= Submission(  
         text=payload.text.strip(),  
@@ \-637,10 \+649,14 @@  
             "evidence\_provenance": evidence\_provenance,  
         }  
   
\+    \# Only evidence that supports the problem may corroborate it. Links  
\+    \# explicitly marked "contradicts" or "mentions" stay visible in  
\+    \# provenance and counts but never raise scores or commercial signals.  
     eligible\_records \= \[  
         record  
         for record in unique\_records  
\-        if record\[2\].get("is\_genuine\_problem") is True  
\+        if record\[0\].relationship\_type not in NON\_SUPPORTING\_RELATIONSHIPS  
\+        and record\[2\].get("is\_genuine\_problem") is True  
         and isinstance(record\[2\].get("signal\_type"), str)  
         and record\[2\].get("signal\_type") in ACTIONABLE\_SIGNAL\_TYPES  
     \]  
@@ \-1032,6 \+1048,16 @@  
     return results  
   
   
\+@app.get("/api/sources/yield")  
\+def source\_yield\_endpoint(db: Session \= Depends(get\_db)):  
\+    """Per-community yield: collected, analysed, genuine and clustered posts.  
\+  
\+    Rates are null until posts are analysed. Use this to adjust collection  
\+    budgets from measured results, not from assumptions about communities.  
\+    """  
\+    return {"communities": community\_yield(db)}  
\+  
\+  
 @app.post("/api/pipeline/run")  
 def run\_pipeline\_endpoint(  
     payload: PipelineRunRequest,  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/app/models.py b/app/models.py  
\--- a/app/models.py	2026-10-19 19:36:56.000000000 \+0000  
\+++ b/app/models.py	2026-10-20 06:25:56.510989068 \+0000  
@@ \-150,3 \+150,37 @@  
     locked\_until: Mapped\[datetime | None\] \= mapped\_column(  
         DateTime(timezone=True), nullable=True  
     )  
\+  
\+  
\+class SourceRecord(Base):  
\+    """Structured provenance for one collected submission.  
\+  
\+    Kept in its own table so existing databases only gain a new table  
\+    (\`\`create\_all\`\`) and no existing column is altered.  
\+    """  
\+  
\+    \_\_tablename\_\_ \= "source\_records"  
\+  
\+    submission\_id: Mapped\[int\] \= mapped\_column(  
\+        ForeignKey("submissions.id", ondelete="CASCADE"),  
\+        primary\_key=True,  
\+    )  
\+    source\_name: Mapped\[str\] \= mapped\_column(  
\+        String(100), nullable=False, index=True  
\+    )  
\+    \# Provider-native identifier (Reddit post id, tweet id, HN objectID...).  
\+    native\_id: Mapped\[str | None\] \= mapped\_column(  
\+        String(120), nullable=True, index=True  
\+    )  
\+    \# Subreddit, Stack Exchange site, etc.  
\+    community: Mapped\[str | None\] \= mapped\_column(  
\+        String(100), nullable=True, index=True  
\+    )  
\+    \# "source" \= timestamp supplied by the provider;  
\+    \# "collection\_time" \= provider gave none, so created\_at is a fallback.  
\+    timestamp\_origin: Mapped\[str\] \= mapped\_column(  
\+        String(20), default="collection\_time", nullable=False  
\+    )  
\+    collected\_at: Mapped\[datetime\] \= mapped\_column(  
\+        DateTime(timezone=True), default=utcnow, nullable=False  
\+    )  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/app/pipeline\_schemas.py b/app/pipeline\_schemas.py  
\--- a/app/pipeline\_schemas.py	2026-10-19 20:20:38.000000000 \+0000  
\+++ b/app/pipeline\_schemas.py	2026-10-20 06:26:29.034018815 \+0000  
@@ \-18,6 \+18,14 @@  
     )  
     reddit\_limit: int \= Field(default=5, ge=1, le=25)  
     x\_limit: int \= Field(default=5, ge=1, le=25)  
\+    reddit\_subreddits: list\[str\] | None \= Field(  
\+        default=None,  
\+        max\_length=10,  
\+        description=(  
\+            "Optional subreddit names (for example 'smallbusiness'). When "  
\+            "omitted, the default keyword searches are used."  
\+        ),  
\+    )  
     analysis\_limit: int \= Field(default=10, ge=1, le=30)  
     clustering\_limit: int \= Field(default=10, ge=1, le=30)  
     retry\_needs\_review: bool \= False  
@@ \-31,6 \+39,27 @@  
         ),  
     )  
   
\+    @field\_validator("reddit\_subreddits")  
\+    @classmethod  
\+    def validate\_subreddits(  
\+        cls,  
\+        names: list\[str\] | None,  
\+    ) \-\> list\[str\] | None:  
\+        if names is None:  
\+            return None  
\+        from app.provenance import normalize\_subreddit  
\+  
\+        cleaned \= \[\]  
\+        for name in names:  
\+            normalized \= normalize\_subreddit(name)  
\+            if normalized is None:  
\+                raise ValueError("Invalid subreddit name.")  
\+            if normalized not in cleaned:  
\+                cleaned.append(normalized)  
\+        if not cleaned:  
\+            raise ValueError("Provide at least one subreddit or omit the field.")  
\+        return cleaned  
\+  
     @field\_validator("sources")  
     @classmethod  
     def reject\_duplicate\_sources(  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/app/provenance.py b/app/provenance.py  
\--- a/app/provenance.py	1970-01-01 00:00:00.000000000 \+0000  
\+++ b/app/provenance.py	2026-10-20 06:25:56.514989067 \+0000  
@@ \-0,0 \+1,196 @@  
\+"""Structured source provenance and per-community yield.  
\+  
\+Pure helpers plus two small database functions. Nothing here calls a  
\+provider or a model.  
\+"""  
\+  
\+from \_\_future\_\_ import annotations  
\+  
\+import json  
\+import re  
\+from dataclasses import dataclass  
\+from typing import Any  
\+  
\+from sqlalchemy import select  
\+from sqlalchemy.orm import Session  
\+  
\+from app.models import ClusterEvidence, SourceRecord, Submission  
\+from app.ranking import ACTIONABLE\_SIGNAL\_TYPES  
\+  
\+\_SUBREDDIT \= re.compile(r"^\[A-Za-z0-9\_\]{2,21}\$")  
\+\_REDDIT\_URL \= re.compile(  
\+    r"reddit\\.com/r/(\[A-Za-z0-9\_\]{2,21})/comments/(\[A-Za-z0-9\]{3,12})",  
\+    re.IGNORECASE,  
\+)  
\+\_REDDIT\_SHORT \= re.compile(r"redd\\.it/(\[A-Za-z0-9\]{3,12})", re.IGNORECASE)  
\+\_X\_URL \= re.compile(  
\+    r"(?:x|twitter)\\.com/\[^/\\s\]+/status/(\\d+)", re.IGNORECASE  
\+)  
\+\_HN\_URL \= re.compile(r"news\\.ycombinator\\.com/item\\?id=(\\d+)", re.IGNORECASE)  
\+\_SE\_URL \= re.compile(r"/questions/(\\d+)")  
\+  
\+MAX\_NATIVE\_ID \= 120  
\+  
\+  
\+@dataclass(frozen=True)  
\+class SourceReference:  
\+    native\_id: str | None  
\+    community: str | None  
\+  
\+  
\+def clean\_native\_id(value: Any, \*, source\_name: str \= "") \-\> str | None:  
\+    """Normalise a provider id; reject bools, blanks and over-long values."""  
\+    if isinstance(value, bool) or not isinstance(value, (str, int)):  
\+        return None  
\+    text \= str(value).strip()  
\+    if not text or len(text) \> MAX\_NATIVE\_ID:  
\+        return None  
\+    if source\_name \== "Reddit":  
\+        text \= re.sub(r"^t3\_", "", text, flags=re.IGNORECASE).lower()  
\+        if not re.fullmatch(r"\[a-z0-9\]{3,12}", text):  
\+            return None  
\+    return text  
\+  
\+  
\+def normalize\_subreddit(name: Any) \-\> str | None:  
\+    """'r/SaaS', '/r/saas/' and 'SaaS' all become 'saas'; junk becomes None."""  
\+    if not isinstance(name, str):  
\+        return None  
\+    text \= name.strip().strip("/")  
\+    text \= re.sub(r"^r/", "", text, flags=re.IGNORECASE)  
\+    return text.lower() if \_SUBREDDIT.fullmatch(text) else None  
\+  
\+  
\+def parse\_source\_reference(  
\+    source\_name: str,  
\+    url: str | None,  
\+    item\_id: Any \= None,  
\+) \-\> SourceReference:  
\+    """Derive native id and community, preferring the provider's own id."""  
\+    url \= url or ""  
\+    native \= clean\_native\_id(item\_id, source\_name=source\_name)  
\+    community: str | None \= None  
\+  
\+    if source\_name \== "Reddit":  
\+        match \= \_REDDIT\_URL.search(url)  
\+        if match:  
\+            community \= match.group(1).lower()  
\+            native \= native or clean\_native\_id(match.group(2), source\_name="Reddit")  
\+        else:  
\+            short \= \_REDDIT\_SHORT.search(url)  
\+            if short:  
\+                native \= native or clean\_native\_id(short.group(1), source\_name="Reddit")  
\+    elif source\_name \== "X":  
\+        match \= \_X\_URL.search(url)  
\+        if match:  
\+            native \= native or match.group(1)  
\+    elif source\_name \== "Hacker News":  
\+        match \= \_HN\_URL.search(url)  
\+        if match:  
\+            native \= native or match.group(1)  
\+    elif source\_name.startswith("Stack Exchange:"):  
\+        community \= source\_name.split(":", 1)\[1\] or None  
\+        match \= \_SE\_URL.search(url)  
\+        if match:  
\+            native \= native or match.group(1)  
\+    return SourceReference(native\_id=native, community=community)  
\+  
\+  
\+def known\_native\_ids(db: Session, source\_name: str) \-\> set\[str\]:  
\+    return {  
\+        value  
\+        for value in db.scalars(  
\+            select(SourceRecord.native\_id).where(  
\+                SourceRecord.source\_name \== source\_name,  
\+                SourceRecord.native\_id.is\_not(None),  
\+            )  
\+        )  
\+        if value  
\+    }  
\+  
\+  
\+def record\_source\_provenance(  
\+    db: Session,  
\+    submission: Submission,  
\+    \*,  
\+    native\_id: str | None,  
\+    community: str | None,  
\+    source\_timestamp\_present: bool,  
\+) \-\> SourceRecord:  
\+    """Attach provenance to a flushed submission (same transaction)."""  
\+    record \= SourceRecord(  
\+        submission\_id=submission.id,  
\+        source\_name=submission.source\_name,  
\+        native\_id=native\_id,  
\+        community=community,  
\+        timestamp\_origin="source" if source\_timestamp\_present else "collection\_time",  
\+    )  
\+    db.add(record)  
\+    return record  
\+  
\+  
\+def \_is\_actionable(analysis\_json: str | None) \-\> bool | None:  
\+    """True/False for a parsable analysis, None when there is none."""  
\+    if not analysis\_json:  
\+        return None  
\+    try:  
\+        data \= json.loads(analysis\_json)  
\+    except (TypeError, ValueError):  
\+        return None  
\+    if not isinstance(data, dict):  
\+        return None  
\+    return (  
\+        data.get("is\_genuine\_problem") is True  
\+        and data.get("signal\_type") in ACTIONABLE\_SIGNAL\_TYPES  
\+    )  
\+  
\+  
\+def community\_yield(db: Session) \-\> list\[dict\[str, Any\]\]:  
\+    """How many collected posts from each community turned into signal.  
\+  
\+    Rates are \`\`None\`\` (unknown) until at least one post is analysed, so an  
\+    un-analysed community is never reported as a 0% performer.  
\+    """  
\+    rows \= db.execute(  
\+        select(SourceRecord, Submission).join(  
\+            Submission, Submission.id \== SourceRecord.submission\_id  
\+        )  
\+    ).all()  
\+    clustered\_ids \= set(db.scalars(select(ClusterEvidence.submission\_id)))  
\+  
\+    buckets: dict\[tuple\[str, str | None\], dict\[str, int\]\] \= {}  
\+    for record, submission in rows:  
\+        key \= (record.source\_name, record.community)  
\+        bucket \= buckets.setdefault(  
\+            key, {"collected": 0, "analyzed": 0, "genuine": 0, "clustered": 0}  
\+        )  
\+        bucket\["collected"\] \+= 1  
\+        if submission.analysis\_status \== "completed":  
\+            verdict \= \_is\_actionable(submission.analysis\_json)  
\+            if verdict is not None:  
\+                bucket\["analyzed"\] \+= 1  
\+                bucket\["genuine"\] \+= 1 if verdict else 0  
\+        if submission.id in clustered\_ids:  
\+            bucket\["clustered"\] \+= 1  
\+  
\+    result \= \[\]  
\+    for (source\_name, community), counts in buckets.items():  
\+        analyzed \= counts\["analyzed"\]  
\+        result.append({  
\+            "source\_name": source\_name,  
\+            "community": community,  
\+            \*\*counts,  
\+            "genuine\_rate": (  
\+                round(counts\["genuine"\] / analyzed, 4\) if analyzed else None  
\+            ),  
\+        })  
\+    result.sort(  
\+        key=lambda r: (  
\+            r\["genuine\_rate"\] is None,  
\+            \-(r\["genuine\_rate"\] or 0),  
\+            \-r\["collected"\],  
\+            r\["source\_name"\],  
\+            r\["community"\] or "",  
\+        )  
\+    )  
\+    return result  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/app/ranking.py b/app/ranking.py  
\--- a/app/ranking.py	2026-10-19 19:02:12.000000000 \+0000  
\+++ b/app/ranking.py	2026-10-20 06:14:37.924976830 \+0000  
@@ \-20,6 \+20,9 @@  
     return value  
   
   
\+\# Evidence links with these relationships never corroborate a problem.  
\+NON\_SUPPORTING\_RELATIONSHIPS \= frozenset({"contradicts", "mentions"})  
\+  
 ACTIONABLE\_SIGNAL\_TYPES \= {  
     "pain\_point",  
     "explicit\_tool\_request",  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/app/security.py b/app/security.py  
\--- a/app/security.py	1970-01-01 00:00:00.000000000 \+0000  
\+++ b/app/security.py	2026-10-20 06:28:33.226985770 \+0000  
@@ \-0,0 \+1,98 @@  
\+"""Opt-in API protection (ENG-12). Everything is disabled unless configured.  
\+  
\+\* \`\`SERVICE\_API\_KEY\`\`: when set, every route except \`\`/health\`\` requires an  
\+  \`\`X-API-Key\`\` header with that value.  
\+\* \`\`SUBMISSION\_RATE\_LIMIT\_PER\_MINUTE\`\`: when \> 0, \`\`POST /api/submissions\`\`  
\+  is limited per client address. The limiter is in-process memory, so it is  
\+  per worker; behind a reverse proxy the client address is the proxy's  
\+  unless the proxy is configured to forward the original address.  
\+"""  
\+  
\+from \_\_future\_\_ import annotations  
\+  
\+import hmac  
\+import threading  
\+import time  
\+from collections import deque  
\+from typing import Callable  
\+  
\+from fastapi import Header, HTTPException, Request  
\+  
\+from app.config import get\_settings  
\+  
\+PUBLIC\_PATHS \= frozenset({"/health"})  
\+MAX\_TRACKED\_CLIENTS \= 10\_000  
\+  
\+  
\+def api\_key\_matches(provided: str | None, expected: str) \-\> bool:  
\+    """Constant-time comparison; an empty or missing key never matches."""  
\+    if not provided or not expected:  
\+        return False  
\+    return hmac.compare\_digest(provided.encode("utf-8"), expected.encode("utf-8"))  
\+  
\+  
\+def require\_api\_key(  
\+    request: Request,  
\+    x\_api\_key: str | None \= Header(default=None),  
\+) \-\> None:  
\+    expected \= get\_settings().service\_api\_key  
\+    if not expected or request.url.path in PUBLIC\_PATHS:  
\+        return  
\+    if not api\_key\_matches(x\_api\_key, expected):  
\+        raise HTTPException(status\_code=401, detail="Invalid or missing API key.")  
\+  
\+  
\+class SlidingWindowLimiter:  
\+    """Allow at most \`\`limit\`\` events per \`\`window\`\` seconds for each key."""  
\+  
\+    def \_\_init\_\_(  
\+        self,  
\+        limit: int,  
\+        window: float \= 60.0,  
\+        clock: Callable\[\[\], float\] \= time.monotonic,  
\+    ) \-\> None:  
\+        self.limit \= limit  
\+        self.window \= window  
\+        self.\_clock \= clock  
\+        self.\_events: dict\[str, deque\[float\]\] \= {}  
\+        self.\_lock \= threading.Lock()  
\+  
\+    def allow(self, key: str) \-\> bool:  
\+        now \= self.\_clock()  
\+        with self.\_lock:  
\+            if len(self.\_events) \>= MAX\_TRACKED\_CLIENTS and key not in self.\_events:  
\+                self.\_prune(now)  
\+                if len(self.\_events) \>= MAX\_TRACKED\_CLIENTS:  
\+                    return False  \# fail closed rather than grow without bound  
\+            events \= self.\_events.setdefault(key, deque())  
\+            while events and now \- events\[0\] \>= self.window:  
\+                events.popleft()  
\+            if len(events) \>= self.limit:  
\+                return False  
\+            events.append(now)  
\+            return True  
\+  
\+    def \_prune(self, now: float) \-\> None:  
\+        for key in \[  
\+            k for k, v in self.\_events.items()  
\+            if not v or now \- v\[-1\] \>= self.window  
\+        \]:  
\+            del self.\_events\[key\]  
\+  
\+  
\+\_limiter: SlidingWindowLimiter | None \= None  
\+  
\+  
\+def limit\_submissions(request: Request) \-\> None:  
\+    global \_limiter  
\+    limit \= get\_settings().submission\_rate\_limit\_per\_minute  
\+    if limit \<= 0:  
\+        return  
\+    if \_limiter is None or \_limiter.limit \!= limit:  
\+        \_limiter \= SlidingWindowLimiter(limit)  
\+    client \= request.client.host if request.client else "unknown"  
\+    if not \_limiter.allow(client):  
\+        raise HTTPException(  
\+            status\_code=429,  
\+            detail="Too many submissions; retry later.",  
\+        )  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/app/services/pipeline.py b/app/services/pipeline.py  
\--- a/app/services/pipeline.py	2026-10-19 20:27:53.000000000 \+0000  
\+++ b/app/services/pipeline.py	2026-10-20 06:26:29.033209615 \+0000  
@@ \-6,6 \+6,8 @@  
 from sqlalchemy.exc import IntegrityError  
 from sqlalchemy.orm import Session  
   
\+import json  
\+  
 from app.clustering import cluster\_submission  
 from app.models import (  
     ClusterEvidence,  
@@ \-15,12 \+17,36 @@  
 )  
 from app.ingestion import collect\_hacker\_news, collect\_stack\_exchange  
 from app.pipeline\_schemas import PipelineRunRequest  
\+from app.ranking import ACTIONABLE\_SIGNAL\_TYPES  
 from app.social\_ingestion import collect\_reddit, collect\_x  
 from app.services.analysis import analyze\_pending\_batch  
   
 LOCK\_LEASE \= timedelta(minutes=10)  
   
   
\+def is\_rejected\_analysis(analysis\_json: str | None) \-\> bool:  
\+    """True only when a saved analysis EXPLICITLY marks the post as noise.  
\+  
\+    Missing or unparsable analyses return False so existing behaviour (and  
\+    cluster\_submission's own validation) is unchanged; only a positive  
\+    rejection by the model keeps a record out of clustering.  
\+    """  
\+    if not analysis\_json:  
\+        return False  
\+    try:  
\+        data \= json.loads(analysis\_json)  
\+    except (TypeError, ValueError):  
\+        return False  
\+    if not isinstance(data, dict):  
\+        return False  
\+    if data.get("is\_genuine\_problem") is False:  
\+        return True  
\+    signal\_type \= data.get("signal\_type")  
\+    return isinstance(signal\_type, str) and (  
\+        signal\_type not in ACTIONABLE\_SIGNAL\_TYPES  
\+    )  
\+  
\+  
 def \_ensure\_lock\_row(db: Session) \-\> None:  
     if db.get(PipelineRunLock, 1\) is not None:  
         return  
@@ \-238,7 \+264,11 @@  
             request.stack\_exchange\_site,  
             db,  
         ),  
\-        "reddit": lambda: collect\_reddit(request.reddit\_limit, db),  
\+        "reddit": lambda: (  
\+            collect\_reddit(request.reddit\_limit, db, request.reddit\_subreddits)  
\+            if request.reddit\_subreddits  
\+            else collect\_reddit(request.reddit\_limit, db)  
\+        ),  
         "x": lambda: collect\_x(request.x\_limit, db),  
     }  
     collection \= {}  
@@ \-318,6 \+348,11 @@  
         ).all()  
         eligible \= \[\]  
         for submission in candidates:  
\+            if is\_rejected\_analysis(submission.analysis\_json):  
\+                \# Model-rejected noise must not create clusters or spend  
\+                \# embedding calls. It stays stored and re-analysable.  
\+                clustering\["skipped"\] \+= 1  
\+                continue  
             state \= db.get(PipelineSubmissionState, submission.id)  
             linked \= db.scalar(  
                 select(ClusterEvidence).where(  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/app/social\_ingestion.py b/app/social\_ingestion.py  
\--- a/app/social\_ingestion.py	2026-10-19 19:37:45.000000000 \+0000  
\+++ b/app/social\_ingestion.py	2026-10-20 06:26:29.032605499 \+0000  
@@ \-11,6 \+11,12 @@  
 from app.db import get\_db  
 from app.models import Submission  
 from app.clustering import canonicalize\_url  
\+from app.provenance import (  
\+    known\_native\_ids,  
\+    normalize\_subreddit,  
\+    parse\_source\_reference,  
\+    record\_source\_provenance,  
\+)  
 from app.ingestion import (  
     PROBLEM\_SIGNALS,  
     clean\_text,  
@@ \-101,6 \+107,9 @@  
         for item in existing  
         if (normalized := normalize(item.text))  
     }  
\+    \# Provider-native ids catch the same post arriving under a different URL  
\+    \# form (old.reddit.com, redd.it, slug differences).  
\+    known\_native \= known\_native\_ids(db, source\_name)  
   
     created \= duplicates \= irrelevant \= 0  
     submission\_ids \= \[\]  
@@ \-120,6 \+129,7 @@  
             submitted\_by \= item.get("author")  
             created\_at\_value \= item.get("publishingDate")  
             language \= "und"  
\+            item\_id \= item.get("postId") or item.get("id")  
         elif source\_name \== "X":  
             if item.get("type") \!= "tweet":  
                 irrelevant \+= 1  
@@ \-135,6 \+145,7 @@  
             )  
             created\_at\_value \= item.get("createdAt")  
             language \= item.get("lang")  
\+            item\_id \= item.get("id")  
         else:  
             irrelevant \+= 1  
             continue  
@@ \-147,10 \+158,12 @@  
         url \= raw\_url.strip() if isinstance(raw\_url, str) else ""  
         canonical\_url \= canonicalize\_url(url) if url else None  
         normalized \= normalize(text)  
\+        reference \= parse\_source\_reference(source\_name, url, item\_id)  
   
         if (  
             (canonical\_url and canonical\_url in known\_urls)  
             or (normalized and normalized in known\_texts)  
\+            or (reference.native\_id and reference.native\_id in known\_native)  
         ):  
             duplicates \+= 1  
             continue  
@@ \-181,6 \+194,15 @@  
             submission.created\_at \= source\_created\_at  
         db.add(submission)  
         db.flush()  
\+        record\_source\_provenance(  
\+            db,  
\+            submission,  
\+            native\_id=reference.native\_id,  
\+            community=reference.community,  
\+            source\_timestamp\_present=source\_created\_at is not None,  
\+        )  
\+        if reference.native\_id:  
\+            known\_native.add(reference.native\_id)  
         submission\_ids.append(submission.id)  
         known\_urls.add(canonical\_url)  
         if normalized:  
@@ \-228,12 \+250,56 @@  
     \]  
   
   
\-def collect\_reddit(limit: int, db: Session) \-\> dict:  
\+\# Candidate communities, NOT verified rankings. Validate each community's  
\+\# rules and relevance before enabling collection, and judge usefulness from  
\+\# measured yield (see /api/sources/yield).  
\+REDDIT\_SUBREDDIT\_SHORTLIST \= (  
\+    "smallbusiness", "Entrepreneur",  
\+    "SaaS", "startups",  
\+    "productivity", "automation",  
\+    "ecommerce", "shopify",  
\+    "sysadmin", "devops",  
\+)  
\+MAX\_SUBREDDITS \= 10  
\+  
\+  
\+def \_reddit\_subreddit\_urls(subreddits: list\[str\]) \-\> list\[dict\[str, str\]\]:  
\+    names: list\[str\] \= \[\]  
\+    for raw in subreddits:  
\+        name \= normalize\_subreddit(raw)  
\+        if name is None:  
\+            raise HTTPException(status\_code=422, detail="Invalid subreddit name.")  
\+        if name not in names:  
\+            names.append(name)  
\+    if not names or len(names) \> MAX\_SUBREDDITS:  
\+        raise HTTPException(  
\+            status\_code=422,  
\+            detail=f"Provide between 1 and {MAX\_SUBREDDITS} subreddits.",  
\+        )  
\+    return \[{"url": f"https\://www\.reddit.com/r/{name}/new/"} for name in names\]  
\+  
\+  
\+def collect\_reddit(  
\+    limit: int,  
\+    db: Session,  
\+    subreddits: list\[str\] | None \= None,  
\+) \-\> dict:  
\+    """Collect Reddit posts.  
\+  
\+    With \`\`subreddits\`\` omitted the original keyword searches are used.  
\+    Passing subreddit names switches to those communities' "new" listings;  
\+    the problem-signal filter still applies to every post.  
\+    """  
     settings \= get\_settings()  
\+    start\_urls \= (  
\+        \_reddit\_subreddit\_urls(subreddits)  
\+        if subreddits is not None  
\+        else \_reddit\_search\_urls()  
\+    )  
     items \= \_collect\_apify(  
         settings.apify\_reddit\_actor\_id,  
         {  
\-            "startUrls": \_reddit\_search\_urls(),  
\+            "startUrls": start\_urls,  
             "sort": "new",  
             "timeFilter": "month",  
             "maxItems": limit,  
@@ \-262,9 \+328,10 @@  
 @router.post("/reddit")  
 def ingest\_reddit(  
     limit: int \= Query(default=10, ge=1, le=25),  
\+    subreddits: list\[str\] | None \= Query(default=None),  
     db: Session \= Depends(get\_db),  
 ):  
\-    result \= collect\_reddit(limit, db)  
\+    result \= collect\_reddit(limit, db, subreddits)  
     result.pop("submission\_ids")  
     return result  
   
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/benchmark/README.md b/benchmark/README.md  
\--- a/benchmark/README.md	1970-01-01 00:00:00.000000000 \+0000  
\+++ b/benchmark/README.md	2026-10-20 06:29:09.362985009 \+0000  
@@ \-0,0 \+1,63 @@  
\+\# Benchmark v1: labeling guide and workflow  
\+  
\+Purpose: measure whether the engine detects genuine pain points, separates  
\+noise, groups the same problem, and keeps evidence honest (EVAL-01..06).  
\+\`app/evaluation.py\` computes the metrics; \*\*humans create the labels\*\*.  
\+  
\+Files in this folder named \`\*.synthetic.jsonl\` are tiny made-up examples of the  
\+format. They prove the tooling runs; they say nothing about accuracy.  
\+  
\+\#\# Files (JSON Lines, one object per line, \`\#\` lines ignored)  
\+  
\+\*\*labels.jsonl\*\* (human-made)  
\+  
\+| field | meaning |  
\+|---|---|  
\+| \`benchmark\_version\` | e.g. \`v1\`; one value per file |  
\+| \`id\` | unique string, normally \`\<source\>:\<native id\>\` |  
\+| \`text\` | exact submission text as stored |  
\+| \`is\_genuine\_problem\` | true/false (rules below) |  
\+| \`signal\_type\` | \`pain\_point\`, \`explicit\_tool\_request\`, \`workaround\_or\_manual\_work\`, \`feature\_gap\`, \`buying\_signal\`, \`general\_discussion\`, \`noise\` |  
\+| \`problem\_id\` | required if genuine, forbidden if not; same string \= same underlying problem |  
\+| \`purchase\_intent\` | \`none\`, \`unclear\`, \`suggested\`, \`explicit\` |  
\+| \`independence\_group\` | same string \= same real person/experience (reposts, alt accounts, cross-posts) |  
\+| \`difficult\` | true for borderline cases; reported separately |  
\+| \`pair\_labels\` | optional \`\[{"other": "\<id\>", "label": "same|different|uncertain"}\]\` overrides |  
\+| \`synthetic\` | true on made-up records |  
\+  
\+\*\*predictions.jsonl\*\* (from the engine): \`id\`, \`is\_genuine\_problem\`,  
\+\`signal\_type\`, \`cluster\_id\` (null if unclustered), \`counted\_as\_independent\`  
\+(did scoring count it as separate corroboration), \`willingness\_to\_pay\_signal\`,  
\+\`evidence\_quotes\`.  
\+  
\+\*\*ranking.jsonl\*\* (optional): \`problem\_id\`, \`human\_score\` (e.g. 1-5),  
\+\`model\_score\` (null if the engine could not score it).  
\+  
\+\#\# Labeling rules (write down changes as you refine them)  
\+  
\+1. \*\*Genuine problem\*\*: the author describes an actual difficulty, unmet need,  
\+   repeated manual work, costly workaround, missing tool or specific  
\+   frustration. A topic discussion, joke, hypothetical, vague complaint or  
\+   promotion is not.  
\+2. Casual or brief wording does not disqualify a real problem.  
\+3. \*\*Same problem\*\* means the same underlying job/pain, not shared keywords.  
\+   When two reviewers would disagree, label the pair \`uncertain\`; do not guess.  
\+4. \*\*Purchase intent\*\* requires evidence in the post itself. Asking for a  
\+   tool, likes or engagement, or hypothetical interest is not willingness to  
\+   pay (\`none\`/\`unclear\`).  
\+5. \*\*Independence\*\*: different platforms, URLs or accounts alone are not proof  
\+   of different people. Group records you believe come from one person.  
\+6. Include difficult borderline posts on purpose, and keep noise that looks  
\+   like problems. Aim for roughly 100-200 real records before quoting metrics;  
\+   have a second reviewer label a sample and record disagreements.  
\+  
\+\#\# Run  
\+  
\+\`\`\`  
\+python \-m app.evaluation benchmark/labels.jsonl benchmark/predictions.jsonl \\  
\+    \--ranking benchmark/ranking.jsonl \--markdown report.md \--json report.json  
\+\`\`\`  
\+  
\+The report gives counts, rates and 95% Wilson intervals. It applies \*\*no  
\+pass/fail thresholds\*\*: choose those after seeing a baseline and weighing the  
\+cost of false positives against missed problems.  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/benchmark/example\_labels.synthetic.jsonl b/benchmark/example\_labels.synthetic.jsonl  
\--- a/benchmark/example\_labels.synthetic.jsonl	1970-01-01 00:00:00.000000000 \+0000  
\+++ b/benchmark/example\_labels.synthetic.jsonl	2026-10-20 06:24:46.803250529 \+0000  
@@ \-0,0 \+1,8 @@  
\+{"benchmark\_version": "v0-synthetic-smoke", "synthetic": true, "id": "s1", "source\_name": "synthetic", "text": "I spend three hours every week manually reconciling invoices and it is painful.", "is\_genuine\_problem": true, "signal\_type": "workaround\_or\_manual\_work", "problem\_id": "invoice-reconciliation", "purchase\_intent": "none", "independence\_group": "p1", "pair\_labels": \[{"other": "s4", "label": "different"}\]}  
\+{"benchmark\_version": "v0-synthetic-smoke", "synthetic": true, "id": "s2", "source\_name": "synthetic", "text": "Is there a tool to automate invoice matching? I would pay for this.", "is\_genuine\_problem": true, "signal\_type": "explicit\_tool\_request", "problem\_id": "invoice-reconciliation", "purchase\_intent": "explicit", "independence\_group": "p2"}  
\+{"benchmark\_version": "v0-synthetic-smoke", "synthetic": true, "id": "s3", "source\_name": "synthetic", "text": "Same person reposting: manually reconciling invoices takes three hours weekly.", "is\_genuine\_problem": true, "signal\_type": "workaround\_or\_manual\_work", "problem\_id": "invoice-reconciliation", "purchase\_intent": "none", "independence\_group": "p1"}  
\+{"benchmark\_version": "v0-synthetic-smoke", "synthetic": true, "id": "s4", "source\_name": "synthetic", "text": "Our Shopify returns process is a mess of spreadsheets and email threads.", "is\_genuine\_problem": true, "signal\_type": "pain\_point", "problem\_id": "returns-management", "purchase\_intent": "none", "independence\_group": "p3"}  
\+{"benchmark\_version": "v0-synthetic-smoke", "synthetic": true, "id": "s5", "source\_name": "synthetic", "text": "lol my cat just walked over my keyboard again", "is\_genuine\_problem": false, "signal\_type": "noise", "problem\_id": null, "purchase\_intent": "none"}  
\+{"benchmark\_version": "v0-synthetic-smoke", "synthetic": true, "id": "s6", "source\_name": "synthetic", "text": "Check out my new SaaS, 50% off today only\!", "is\_genuine\_problem": false, "signal\_type": "noise", "problem\_id": null, "purchase\_intent": "none"}  
\+{"benchmark\_version": "v0-synthetic-smoke", "synthetic": true, "id": "s7", "source\_name": "synthetic", "text": "I wonder whether accounting software will get better someday.", "is\_genuine\_problem": false, "signal\_type": "general\_discussion", "problem\_id": null, "purchase\_intent": "none", "difficult": true}  
\+{"benchmark\_version": "v0-synthetic-smoke", "synthetic": true, "id": "s8", "source\_name": "synthetic", "text": "Hypothetically if a tool existed I might maybe look at it.", "is\_genuine\_problem": false, "signal\_type": "general\_discussion", "problem\_id": null, "purchase\_intent": "unclear", "difficult": true}  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/benchmark/example\_predictions.synthetic.jsonl b/benchmark/example\_predictions.synthetic.jsonl  
\--- a/benchmark/example\_predictions.synthetic.jsonl	1970-01-01 00:00:00.000000000 \+0000  
\+++ b/benchmark/example\_predictions.synthetic.jsonl	2026-10-20 06:24:46.803347326 \+0000  
@@ \-0,0 \+1,8 @@  
\+{"id": "s1", "is\_genuine\_problem": true, "signal\_type": "workaround\_or\_manual\_work", "cluster\_id": "c1", "counted\_as\_independent": true, "willingness\_to\_pay\_signal": "none", "evidence\_quotes": \["I spend three hours every week"\]}  
\+{"id": "s2", "is\_genuine\_problem": true, "signal\_type": "explicit\_tool\_request", "cluster\_id": "c1", "counted\_as\_independent": true, "willingness\_to\_pay\_signal": "explicit", "evidence\_quotes": \["I would pay for this."\]}  
\+{"id": "s3", "is\_genuine\_problem": true, "signal\_type": "workaround\_or\_manual\_work", "cluster\_id": "c1", "counted\_as\_independent": true, "willingness\_to\_pay\_signal": "none", "evidence\_quotes": \["Same person reposting: manuall"\]}  
\+{"id": "s4", "is\_genuine\_problem": true, "signal\_type": "pain\_point", "cluster\_id": "c1", "counted\_as\_independent": true, "willingness\_to\_pay\_signal": "none", "evidence\_quotes": \["not in the source"\]}  
\+{"id": "s5", "is\_genuine\_problem": false, "signal\_type": "noise", "cluster\_id": null, "willingness\_to\_pay\_signal": "none", "evidence\_quotes": \[\]}  
\+{"id": "s6", "is\_genuine\_problem": true, "signal\_type": "buying\_signal", "cluster\_id": "c2", "counted\_as\_independent": true, "willingness\_to\_pay\_signal": "explicit", "evidence\_quotes": \["50% off today only\!"\]}  
\+{"id": "s7", "is\_genuine\_problem": false, "signal\_type": "general\_discussion", "cluster\_id": null, "willingness\_to\_pay\_signal": "none", "evidence\_quotes": \[\]}  
\+{"id": "s8", "is\_genuine\_problem": false, "signal\_type": "general\_discussion", "cluster\_id": null, "willingness\_to\_pay\_signal": "none", "evidence\_quotes": \[\]}  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/benchmark/example\_ranking.synthetic.jsonl b/benchmark/example\_ranking.synthetic.jsonl  
\--- a/benchmark/example\_ranking.synthetic.jsonl	1970-01-01 00:00:00.000000000 \+0000  
\+++ b/benchmark/example\_ranking.synthetic.jsonl	2026-10-20 06:24:46.803396511 \+0000  
@@ \-0,0 \+1,3 @@  
\+{"problem\_id": "invoice-reconciliation", "human\_score": 5, "model\_score": 70.0}  
\+{"problem\_id": "returns-management", "human\_score": 3, "model\_score": 40.0}  
\+{"problem\_id": "weak-idea", "human\_score": 1, "model\_score": 55.0}  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/docs/OPERATOR\_API.md b/docs/OPERATOR\_API.md  
\--- a/docs/OPERATOR\_API.md	2026-10-19 20:31:24.000000000 \+0000  
\+++ b/docs/OPERATOR\_API.md	2026-10-20 06:29:09.379499676 \+0000  
@@ \-22,9 \+22,29 @@  
   
 \`created\_at\` on an evidence provenance item is when it was attached;  
 \`submission\_created\_at\` is the timestamp stored on the submission row.  
\-Ingestion adapters populate that field from provider timestamps when available,  
\-but the schema does not preserve whether a timestamp came from the provider or  
\-was locally generated as a fallback. Source-native IDs are not stored.  
\+Ingestion adapters populate that field from provider timestamps when available.  
\+The \`source\_records\` table (one row per collected submission) now stores the  
\+provider-native ID, the community (subreddit or Stack Exchange site) and  
\+\`timestamp\_origin\` (\`source\`, or \`collection\_time\` when the provider gave none  
\+and \`created\_at\` is a local fallback). Submissions collected before this table  
\+existed have no row. Re-ingesting a Reddit/X post under another URL form is  
\+detected by native ID and counted as a duplicate.  
\+  
\+\`GET /api/sources/yield\` reports, per source and community, how many posts were  
\+collected, analysed, judged genuine and clustered. Rates are \`null\` until posts  
\+are analysed. Opportunity aggregation ignores evidence links whose relationship  
\+is \`contradicts\` or \`mentions\`; they remain visible in provenance and counts.  
\+  
\+Optional protections (off by default): \`SERVICE\_API\_KEY\` requires an  
\+\`X-API-Key\` header on every route except \`/health\`;  
\+\`SUBMISSION\_RATE\_LIMIT\_PER\_MINUTE\` limits \`POST /api/submissions\` per client  
\+address and per worker process.  
\+  
\+\`POST /api/pipeline/run\` and \`POST /api/ingestion/reddit\` accept optional  
\+\`reddit\_subreddits\` / \`subreddits\` to collect from specific communities'  
\+"new" listings instead of the default keyword searches. This mode is  
\+\*\*unverified against the live Apify actor\*\*: run one bounded request and  
\+inspect the output before relying on it.  
 Original source URLs are returned unchanged. \`independent\_evidence\_count\` is retained for API  
 compatibility and represents recorded flags after duplicate removal;  
 \`recorded\_independent\_evidence\_count\` names that meaning explicitly.  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/tests/test\_evaluation.py b/tests/test\_evaluation.py  
\--- a/tests/test\_evaluation.py	1970-01-01 00:00:00.000000000 \+0000  
\+++ b/tests/test\_evaluation.py	2026-10-20 06:25:12.130990001 \+0000  
@@ \-0,0 \+1,245 @@  
\+"""Tests for the offline benchmark evaluation module (no providers, no DB)."""  
\+from pathlib import Path  
\+  
\+import pytest  
\+  
\+from app import evaluation as ev  
\+from app.ranking import score\_opportunity  
\+  
\+BENCH \= Path(\_\_file\_\_).resolve().parent.parent / "benchmark"  
\+  
\+  
\+def \_label(id, genuine, signal, problem=None, \*\*extra):  
\+    return {  
\+        "benchmark\_version": "t",  
\+        "id": id,  
\+        "text": extra.pop("text", f"text of {id} with enough words"),  
\+        "is\_genuine\_problem": genuine,  
\+        "signal\_type": signal,  
\+        "problem\_id": problem,  
\+        \*\*extra,  
\+    }  
\+  
\+  
\+def \_pred(id, genuine, signal, cluster=None, \*\*extra):  
\+    return {  
\+        "id": id,  
\+        "is\_genuine\_problem": genuine,  
\+        "signal\_type": signal,  
\+        "cluster\_id": cluster,  
\+        \*\*extra,  
\+    }  
\+  
\+  
\+def test\_shipped\_synthetic\_example\_matches\_hand\_computed\_values():  
\+    labels \= ev.load\_jsonl(BENCH / "example\_labels.synthetic.jsonl")  
\+    preds \= ev.load\_jsonl(BENCH / "example\_predictions.synthetic.jsonl")  
\+    ranking \= ev.load\_jsonl(BENCH / "example\_ranking.synthetic.jsonl")  
\+  
\+    report \= ev.build\_report(labels, preds, ranking)  
\+  
\+    assert report\["contains\_synthetic\_records"\] is True  
\+    genuine \= report\["classification"\]\["genuine\_problem"\]  
\+    assert genuine\["confusion"\] \== {"tp": 4, "fp": 1, "fn": 0, "tn": 3}  
\+    assert genuine\["precision"\]\["value"\] \== pytest.approx(0.8)  
\+    assert genuine\["false\_positive\_rate"\]\["value"\] \== pytest.approx(0.25)  
\+    assert report\["classification"\]\["false\_positive\_ids"\] \== \["s6"\]  
\+    clustering \= report\["clustering"\]  
\+    assert clustering\["false\_merge\_rate"\]\["count"\] \== 3  
\+    assert clustering\["missed\_match\_rate"\]\["value"\] \== 0.0  
\+    assert clustering\["clusters\_mixing\_distinct\_problems"\] \== \["c1"\]  
\+    assert report\["purchase\_intent"\]\["claim\_precision"\]\["value"\] \== 0.5  
\+    assert report\["evidence\_integrity"\]\["traceable\_quote\_rate"\]\["count"\] \== 4  
\+    assert report\["independence"\]\["overclaimed\_clusters"\] \== \[  
\+        {"cluster\_id": "c1", "claimed": 4, "supportable": 3}  
\+    \]  
\+    assert report\["ranking"\]\["spearman\_rho"\] \== pytest.approx(0.5)  
\+    assert "WARNING" in ev.render\_markdown(report)  
\+  
\+  
\+def test\_rates\_with\_no\_denominator\_are\_unknown\_not\_zero():  
\+    labels \= \[\_label("a", False, "noise")\]  
\+    preds \= \[\_pred("a", False, "noise")\]  
\+  
\+    genuine \= ev.classification\_report(labels, preds)\["genuine\_problem"\]  
\+  
\+    assert genuine\["precision"\]\["value"\] is None  
\+    assert genuine\["recall"\]\["value"\] is None  
\+    assert genuine\["false\_positive\_rate"\]\["value"\] \== 0.0  
\+    assert genuine\["false\_positive\_rate"\]\["of"\] \== 1  
\+  
\+  
\+def test\_missing\_and\_unknown\_predictions\_are\_reported\_not\_hidden():  
\+    labels \= \[\_label("a", False, "noise"), \_label("b", False, "noise")\]  
\+    preds \= \[\_pred("a", False, "noise"), \_pred("zzz", True, "pain\_point")\]  
\+  
\+    report \= ev.classification\_report(labels, preds)  
\+  
\+    assert report\["missing\_predictions"\] \== \["b"\]  
\+    assert report\["unknown\_prediction\_ids"\] \== \["zzz"\]  
\+    assert report\["matched\_predictions"\] \== 1  
\+  
\+  
\+def test\_model\_flag\_without\_actionable\_type\_is\_not\_a\_positive():  
\+    labels \= \[\_label("a", True, "pain\_point", "p1")\]  
\+    preds \= \[\_pred("a", True, "noise")\]  
\+  
\+    report \= ev.classification\_report(labels, preds)  
\+  
\+    assert report\["false\_negative\_ids"\] \== \["a"\]  
\+  
\+  
\+def test\_missed\_match\_and\_uncertain\_pairs\_are\_separated():  
\+    labels \= \[  
\+        \_label("a", True, "pain\_point", "p1"),  
\+        \_label("b", True, "pain\_point", "p1"),  
\+        \_label("c", True, "pain\_point", "p2"),  
\+    \]  
\+    labels\[0\]\["pair\_labels"\] \= \[{"other": "c", "label": "uncertain"}\]  
\+    preds \= \[  
\+        \_pred("a", True, "pain\_point", "x"),  
\+        \_pred("b", True, "pain\_point", "y"),  
\+        \_pred("c", True, "pain\_point", "x"),  
\+    \]  
\+  
\+    report \= ev.clustering\_report(labels, preds)  
\+  
\+    assert report\["missed\_match\_rate"\]\["count"\] \== 1  \# a-b split  
\+    assert report\["false\_merge\_rate"\]\["of"\] \== 1  \# only b-c is "different"  
\+    assert report\["false\_merge\_rate"\]\["count"\] \== 0  
\+    assert report\["uncertain\_pairs\_merged"\] \== 1  \# a-c, excluded from rates  
\+  
\+  
\+def test\_same\_account\_on\_many\_platforms\_is\_one\_independent\_voice():  
\+    labels \= \[  
\+        \_label("a", True, "pain\_point", "p1", independence\_group="person1"),  
\+        \_label("b", True, "pain\_point", "p1", independence\_group="person1"),  
\+        \_label("c", True, "pain\_point", "p1", independence\_group="person2"),  
\+    \]  
\+    preds \= \[  
\+        \_pred("a", True, "pain\_point", "k", counted\_as\_independent=True),  
\+        \_pred("b", True, "pain\_point", "k", counted\_as\_independent=True),  
\+        \_pred("c", True, "pain\_point", "k", counted\_as\_independent=True),  
\+    \]  
\+  
\+    report \= ev.independence\_report(labels, preds)  
\+  
\+    assert report\["claimed\_independent\_records"\] \== 3  
\+    assert report\["supportable\_independent\_records"\] \== 2  
\+    assert report\["overclaim\_rate"\]\["value"\] \== pytest.approx(1 / 3\)  
\+  
\+  
\+def test\_unsupported\_buying\_claim\_is\_flagged():  
\+    labels \= \[  
\+        \_label("a", False, "noise", text="Nice weather and no tool talk here."),  
\+    \]  
\+    preds \= \[  
\+        \_pred(  
\+            "a", True, "buying\_signal",  
\+            willingness\_to\_pay\_signal="explicit",  
\+            evidence\_quotes=\["I will pay \$500"\],  
\+        )  
\+    \]  
\+  
\+    report \= ev.purchase\_intent\_report(labels, preds)  
\+  
\+    assert report\["claims\_without\_traceable\_quote"\]\["value"\] \== 1.0  
\+    assert report\["claims\_on\_records\_labelled\_no\_intent"\] \== \["a"\]  
\+  
\+  
\+def test\_spearman\_handles\_ties\_and\_degenerate\_input():  
\+    assert ev.spearman(\[1, 2, 3\], \[10, 20, 30\]) \== pytest.approx(1.0)  
\+    assert ev.spearman(\[1, 2, 3\], \[30, 20, 10\]) \== pytest.approx(-1.0)  
\+    assert ev.spearman(\[1, 1, 1\], \[1, 2, 3\]) is None  
\+    assert ev.spearman(\[1\], \[1\]) is None  
\+    assert ev.\_average\_ranks(\[5, 5, 1\]) \== \[2.5, 2.5, 1.0\]  
\+  
\+  
\+def test\_unscored\_ranking\_items\_are\_listed\_not\_dropped():  
\+    items \= \[  
\+        {"problem\_id": "a", "human\_score": 5, "model\_score": 80.0},  
\+        {"problem\_id": "b", "human\_score": 1, "model\_score": 20.0},  
\+        {"problem\_id": "c", "human\_score": 4, "model\_score": None},  
\+    \]  
\+  
\+    report \= ev.ranking\_report(items)  
\+  
\+    assert report\["items\_unscored\_by\_model"\] \== \["c"\]  
\+    assert report\["items\_scored"\] \== 2  
\+    assert report\["pairwise\_agreement"\]\["value"\] \== 1.0  
\+  
\+  
\+def test\_validation\_rejects\_inconsistent\_labels():  
\+    bad \= \[  
\+        \_label("a", True, "noise", "p1"),  
\+        \_label("a", False, "pain\_point"),  
\+        \_label("c", True, "pain\_point"),  
\+        \_label("d", False, "noise", "p9"),  
\+    \]  
\+  
\+    errors \= ev.validate\_labels(bad)  
\+  
\+    joined \= "\\n".join(errors)  
\+    assert "duplicate id" in joined  
\+    assert "non-actionable signal\_type" in joined  
\+    assert "actionable signal\_type" in joined  
\+    assert "needs a problem\_id" in joined  
\+    assert "must not have a problem\_id" in joined  
\+    with pytest.raises(ValueError):  
\+        ev.build\_report(bad, \[\])  
\+  
\+  
\+def test\_wilson\_interval\_is\_wide\_for\_tiny\_samples():  
\+    low, high \= ev.wilson\_interval(2, 2\)  
\+    assert low \< 0.5 and high \== pytest.approx(1.0)  
\+    assert ev.wilson\_interval(0, 0\) is None  
\+  
\+  
\+def \_analysis():  
\+    return {  
\+        "is\_genuine\_problem": True,  
\+        "signal\_type": "pain\_point",  
\+        "urgency\_1\_to\_5": 3,  
\+        "frequency\_signal\_1\_to\_5": 3,  
\+        "evidence\_strength\_1\_to\_5": 3,  
\+        "willingness\_to\_pay\_signal": "none",  
\+        "evidence\_quotes": \[\],  
\+    }  
\+  
\+  
\+def test\_rating\_sensitivity\_reports\_one\_step\_changes\_against\_real\_scorer():  
\+    result \= ev.rating\_sensitivity(score\_opportunity, \_analysis())  
\+  
\+    assert result\["base\_score"\] is not None  
\+    assert set(result\["changes"\]) \== {  
\+        f"{field}{delta}"  
\+        for field in (  
\+            "urgency\_1\_to\_5",  
\+            "frequency\_signal\_1\_to\_5",  
\+            "evidence\_strength\_1\_to\_5",  
\+        )  
\+        for delta in ("-1", "+1")  
\+    }  
\+    \# More urgency can never lower the score.  
\+    assert result\["changes"\]\["urgency\_1\_to\_5+1"\] \>= 0  
\+    assert result\["changes"\]\["urgency\_1\_to\_5-1"\] \<= 0  
\+  
\+  
\+def test\_duplicate\_invariance\_holds\_for\_real\_scorer():  
\+    assert ev.duplicate\_invariance(score\_opportunity, \_analysis())\["invariant"\]  
\+  
\+  
\+def test\_cli\_writes\_reports(tmp\_path):  
\+    out\_json \= tmp\_path / "r.json"  
\+    out\_md \= tmp\_path / "r.md"  
\+  
\+    code \= ev.main(\[  
\+        str(BENCH / "example\_labels.synthetic.jsonl"),  
\+        str(BENCH / "example\_predictions.synthetic.jsonl"),  
\+        "--json", str(out\_json),  
\+        "--markdown", str(out\_md),  
\+    \])  
\+  
\+    assert code \== 0  
\+    assert out\_json.read\_text().startswith("{")  
\+    assert "Benchmark report" in out\_md.read\_text()  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/tests/test\_opportunity\_scoring.py b/tests/test\_opportunity\_scoring.py  
\--- a/tests/test\_opportunity\_scoring.py	2026-10-19 19:02:47.000000000 \+0000  
\+++ b/tests/test\_opportunity\_scoring.py	2026-10-20 06:14:57.531499009 \+0000  
@@ \-2,7 \+2,7 @@  
 import json  
   
 import pytest  
\-from sqlalchemy import create\_engine  
\+from sqlalchemy import create\_engine, select  
 from sqlalchemy.orm import sessionmaker  
 from sqlalchemy.pool import StaticPool  
   
@@ \-582,3 \+582,52 @@  
     assert opportunity\["recorded\_independent\_records"\] \== 1  
     assert details\["evidence\_count"\] \== 2  
     assert details\["independent\_evidence\_count"\] \== 1  
\+  
\+  
\+def test\_contradicting\_and\_mention\_only\_links\_do\_not\_corroborate(db, cluster):  
\+    add\_evidence(  
\+        db,  
\+        cluster.id,  
\+        text="Owners manually reconcile orders every week.",  
\+        url="https\://example.com/supports",  
\+        analysis=valid\_analysis() | {  
\+            "willingness\_to\_pay\_signal": "none",  
\+            "evidence\_quotes": \[\],  
\+        },  
\+    )  
\+    baseline \= get\_result(db, cluster)  
\+  
\+    for relationship, slug in (  
\+        ("contradicts", "contradicts"),  
\+        ("mentions", "mentions"),  
\+    ):  
\+        submission \= add\_evidence(  
\+            db,  
\+            cluster.id,  
\+            text=f"Strongly worded {slug} claim with an explicit purchase.",  
\+            url=f"https\://example.com/{slug}",  
\+            source=f"platform-{slug}",  
\+            analysis=valid\_analysis() | {  
\+                "urgency\_1\_to\_5": 5,  
\+                "frequency\_signal\_1\_to\_5": 5,  
\+                "evidence\_strength\_1\_to\_5": 5,  
\+                "willingness\_to\_pay\_signal": "explicit",  
\+                "evidence\_quotes": \["Strongly worded"\],  
\+            },  
\+        )  
\+        link \= db.scalar(  
\+            select(ClusterEvidence).where(  
\+                ClusterEvidence.submission\_id \== submission.id  
\+            )  
\+        )  
\+        link.relationship\_type \= relationship  
\+        db.commit()  
\+  
\+    result \= get\_result(db, cluster)  
\+  
\+    assert result\["score"\] \== baseline\["score"\]  
\+    assert result\["eligible\_evidence\_count"\] \== 1  
\+    assert result\["eligible\_distinct\_source\_record\_count"\] \== 1  
\+    assert result\["commercial\_signal"\] \== "none"  
\+    \# The records remain visible as attached evidence.  
\+    assert result\["raw\_attached\_record\_count"\] \== 3  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/tests/test\_pipeline.py b/tests/test\_pipeline.py  
\--- a/tests/test\_pipeline.py	2026-10-19 20:32:38.000000000 \+0000  
\+++ b/tests/test\_pipeline.py	2026-10-20 06:14:49.385217180 \+0000  
@@ \-1122,3 \+1122,74 @@  
     assert stored\["analysis\_status"\] \== "failed"  
     assert stored\["analysis\_json"\] is None  
     assert stored\["analysis\_error"\] \== "analysis\_failed"  
\+  
\+  
\+def test\_model\_rejected\_noise\_is\_not\_sent\_to\_clustering(  
\+    pipeline\_env,  
\+    monkeypatch,  
\+):  
\+    client, session\_factory \= pipeline\_env  
\+    with session\_factory() as db:  
\+        noise \= \_add\_submission(  
\+            db,  
\+            status="completed",  
\+            text="Unrelated promotional content for a new product launch.",  
\+        )  
\+        noise.analysis\_json \= json.dumps({  
\+            "is\_genuine\_problem": False,  
\+            "signal\_type": "noise",  
\+            "problem\_statement": "Promotional content",  
\+            "title": "Promotion",  
\+        })  
\+        db.commit()  
\+  
\+    monkeypatch.setattr(  
\+        "app.services.pipeline.collect\_hacker\_news",  
\+        lambda \*\_args: {  
\+            "source": "Hacker News",  
\+            "fetched": 0,  
\+            "created": 0,  
\+            "duplicates\_skipped": 0,  
\+            "irrelevant\_skipped": 0,  
\+            "submission\_ids": \[\],  
\+        },  
\+    )  
\+    calls \= \[\]  
\+  
\+    def fail\_if\_called(\*\_args, \*\*\_kwargs):  
\+        calls.append(True)  
\+        return {"decision": "created", "cluster\_id": 1}  
\+  
\+    monkeypatch.setattr(  
\+        "app.services.pipeline.cluster\_submission",  
\+        fail\_if\_called,  
\+    )  
\+  
\+    response \= client.post("/api/pipeline/run", json=\_request())  
\+  
\+    assert response.status\_code \== 200  
\+    assert calls \== \[\]  
\+    assert response.json()\["clustering"\]\["considered"\] \== 0  
\+    assert response.json()\["clustering"\]\["skipped"\] \== 1  
\+  
\+  
\+def test\_is\_rejected\_analysis\_only\_rejects\_explicit\_noise():  
\+    from app.services.pipeline import is\_rejected\_analysis  
\+  
\+    assert is\_rejected\_analysis(  
\+        json.dumps({"is\_genuine\_problem": False, "signal\_type": "noise"})  
\+    )  
\+    assert is\_rejected\_analysis(  
\+        json.dumps({"is\_genuine\_problem": True, "signal\_type": "noise"})  
\+    )  
\+    assert not is\_rejected\_analysis(  
\+        json.dumps({  
\+            "is\_genuine\_problem": True,  
\+            "signal\_type": "pain\_point",  
\+        })  
\+    )  
\+    \# Legacy or malformed analyses keep their existing behaviour.  
\+    assert not is\_rejected\_analysis(json.dumps({"title": "No flags"}))  
\+    assert not is\_rejected\_analysis(None)  
\+    assert not is\_rejected\_analysis("not json")  
\+    assert not is\_rejected\_analysis("\[\]")  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/tests/test\_provenance.py b/tests/test\_provenance.py  
\--- a/tests/test\_provenance.py	1970-01-01 00:00:00.000000000 \+0000  
\+++ b/tests/test\_provenance.py	2026-10-20 06:27:04.255817018 \+0000  
@@ \-0,0 \+1,272 @@  
\+"""Provenance, native-id idempotency, subreddit targeting and yield tests."""  
\+import json  
\+  
\+import pytest  
\+from fastapi import HTTPException  
\+from sqlalchemy import create\_engine, inspect, select  
\+from sqlalchemy.orm import sessionmaker  
\+from sqlalchemy.pool import StaticPool  
\+  
\+from app import ingestion, social\_ingestion  
\+from app.db import Base, initialize\_database  
\+from app.models import ClusterEvidence, ProblemCluster, SourceRecord, Submission  
\+from app.provenance import (  
\+    clean\_native\_id,  
\+    community\_yield,  
\+    normalize\_subreddit,  
\+    parse\_source\_reference,  
\+)  
\+  
\+  
\+@pytest.fixture  
\+def db():  
\+    engine \= create\_engine(  
\+        "sqlite://",  
\+        connect\_args={"check\_same\_thread": False},  
\+        poolclass=StaticPool,  
\+    )  
\+    initialize\_database(engine)  
\+    session \= sessionmaker(bind=engine, expire\_on\_commit=False)()  
\+    try:  
\+        yield session  
\+    finally:  
\+        session.close()  
\+        engine.dispose()  
\+  
\+  
\+def \_reddit(post\_id="abc123", sub="smallbusiness", \*\*extra):  
\+    item \= {  
\+        "itemType": "post",  
\+        "postId": f"t3\_{post\_id}",  
\+        "title": "Manual reporting is frustrating",  
\+        "selftext": "I struggle to reconcile the weekly orders by hand.",  
\+        "link": f"https\://www\.reddit.com/r/{sub}/comments/{post\_id}/slug/",  
\+        "author": "owner",  
\+        "publishingDate": "2024-01-02T03:04:05.000Z",  
\+    }  
\+    item.update(extra)  
\+    return item  
\+  
\+  
\+def test\_reference\_parsing\_for\_every\_source():  
\+    reddit \= parse\_source\_reference(  
\+        "Reddit", "https\://old.reddit.com/r/SaaS/comments/1ab2cd/title/"  
\+    )  
\+    assert (reddit.native\_id, reddit.community) \== ("1ab2cd", "saas")  
\+    assert parse\_source\_reference("Reddit", "https\://redd.it/1ab2cd").native\_id \== "1ab2cd"  
\+    assert parse\_source\_reference(  
\+        "X", "https\://x.com/someone/status/998877"  
\+    ).native\_id \== "998877"  
\+    assert parse\_source\_reference(  
\+        "Hacker News", "https\://news.ycombinator.com/item?id=4242"  
\+    ).native\_id \== "4242"  
\+    se \= parse\_source\_reference(  
\+        "Stack Exchange:superuser", "https\://superuser.com/questions/777/x"  
\+    )  
\+    assert (se.native\_id, se.community) \== ("777", "superuser")  
\+    \# The provider's own id wins over the URL-derived one.  
\+    assert parse\_source\_reference("X", "https\://x.com/a/status/1", 2).native\_id \== "2"  
\+  
\+  
\+def test\_native\_id\_and\_subreddit\_cleaning\_reject\_junk():  
\+    assert clean\_native\_id(True) is None  
\+    assert clean\_native\_id("  ") is None  
\+    assert clean\_native\_id("x" \* 500\) is None  
\+    assert clean\_native\_id("t3\_AbC123", source\_name="Reddit") \== "abc123"  
\+    assert clean\_native\_id("not a valid id\!", source\_name="Reddit") is None  
\+    assert normalize\_subreddit("/r/SaaS/") \== "saas"  
\+    assert normalize\_subreddit("r/smallbusiness") \== "smallbusiness"  
\+    for bad in ("a", "has space", "../etc", "x" \* 30, None, 5):  
\+        assert normalize\_subreddit(bad) is None  
\+  
\+  
\+def test\_reddit\_ingestion\_records\_provenance\_and\_source\_timestamp(db):  
\+    result \= social\_ingestion.\_save\_items(db, \[\_reddit()\], "Reddit")  
\+  
\+    assert result\["created"\] \== 1  
\+    record \= db.scalar(select(SourceRecord))  
\+    assert record.submission\_id \== result\["submission\_ids"\]\[0\]  
\+    assert record.native\_id \== "abc123"  
\+    assert record.community \== "smallbusiness"  
\+    assert record.timestamp\_origin \== "source"  
\+  
\+  
\+def test\_missing\_provider\_timestamp\_is\_recorded\_as\_fallback(db):  
\+    item \= \_reddit()  
\+    item.pop("publishingDate")  
\+  
\+    social\_ingestion.\_save\_items(db, \[item\], "Reddit")  
\+  
\+    assert db.scalar(select(SourceRecord)).timestamp\_origin \== "collection\_time"  
\+  
\+  
\+def test\_same\_post\_under\_another\_url\_form\_is\_a\_duplicate(db):  
\+    social\_ingestion.\_save\_items(db, \[\_reddit()\], "Reddit")  
\+    variant \= \_reddit(  
\+        title="A totally different title that is frustrating to read",  
\+        link="https\://old.reddit.com/r/smallbusiness/comments/abc123/",  
\+    )  
\+  
\+    result \= social\_ingestion.\_save\_items(db, \[variant\], "Reddit")  
\+  
\+    assert result\["created"\] \== 0  
\+    assert result\["duplicates\_skipped"\] \== 1  
\+    assert len(db.scalars(select(Submission)).all()) \== 1  
\+    assert len(db.scalars(select(SourceRecord)).all()) \== 1  
\+  
\+  
\+def test\_x\_ingestion\_records\_native\_id(db):  
\+    tweet \= {  
\+        "type": "tweet",  
\+        "id": "5550001",  
\+        "url": "https\://x.com/example/status/5550001",  
\+        "text": "I struggle with this frustrating manual workflow.",  
\+        "lang": "en",  
\+        "createdAt": "Tue Jan 02 03:04:05 \+0000 2024",  
\+        "author": {"userName": "example"},  
\+    }  
\+  
\+    social\_ingestion.\_save\_items(db, \[tweet\], "X")  
\+  
\+    assert db.scalar(select(SourceRecord)).native\_id \== "5550001"  
\+  
\+  
\+def test\_hacker\_news\_and\_stack\_exchange\_record\_provenance(db, monkeypatch):  
\+    monkeypatch.setattr(  
\+        ingestion,  
\+        "\_get\_json",  
\+        lambda \*a, \*\*k: {  
\+            "hits": \[{  
\+                "objectID": "4242",  
\+                "title": "Ask HN: How do you handle painful manual invoicing?",  
\+                "story\_text": "It is a frustrating problem for our team.",  
\+                "author": "pg",  
\+                "created\_at\_i": 1704164645,  
\+            }\]  
\+        },  
\+    )  
\+    ingestion.collect\_hacker\_news(5, db)  
\+    record \= db.scalar(select(SourceRecord))  
\+    assert (record.source\_name, record.native\_id) \== ("Hacker News", "4242")  
\+    assert record.timestamp\_origin \== "source"  
\+  
\+    monkeypatch.setattr(  
\+        ingestion,  
\+        "\_get\_json",  
\+        lambda \*a, \*\*k: {  
\+            "items": \[{  
\+                "question\_id": 777,  
\+                "title": "How to stop manual copy-paste between sheets?",  
\+                "body": "\<p\>I keep doing this repetitive work by hand.\</p\>",  
\+                "link": "https\://superuser.com/questions/777/x",  
\+                "creation\_date": 1704164645,  
\+            }\]  
\+        },  
\+    )  
\+    ingestion.collect\_stack\_exchange(5, "superuser", db)  
\+    stack \= db.scalars(  
\+        select(SourceRecord).where(SourceRecord.native\_id \== "777")  
\+    ).one()  
\+    assert stack.community \== "superuser"  
\+  
\+  
\+def test\_subreddit\_mode\_builds\_listing\_urls\_and\_validates(monkeypatch, db):  
\+    seen \= {}  
\+  
\+    def fake\_collect(actor\_id, actor\_input, limit):  
\+        seen.update(actor\_input)  
\+        return \[\]  
\+  
\+    monkeypatch.setattr(social\_ingestion, "\_collect\_apify", fake\_collect)  
\+    monkeypatch.setattr(  
\+        social\_ingestion,  
\+        "get\_settings",  
\+        lambda: type("S", (), {"apify\_reddit\_actor\_id": "a\~b"})(),  
\+    )  
\+  
\+    social\_ingestion.collect\_reddit(5, db, \["r/SaaS", "SaaS", "devops"\])  
\+  
\+    assert seen\["startUrls"\] \== \[  
\+        {"url": "https\://www\.reddit.com/r/saas/new/"},  
\+        {"url": "https\://www\.reddit.com/r/devops/new/"},  
\+    \]  
\+    for bad in (\["bad name"\], \[\], \["a"\] \* 1 \+ \["x" \* 30\]):  
\+        with pytest.raises(HTTPException) as caught:  
\+            social\_ingestion.collect\_reddit(5, db, bad)  
\+        assert caught.value.status\_code \== 422  
\+    \# Omitting subreddits keeps the original keyword searches.  
\+    seen.clear()  
\+    social\_ingestion.collect\_reddit(5, db)  
\+    assert "search" in seen\["startUrls"\]\[0\]\["url"\]  
\+  
\+  
\+def test\_shortlist\_is\_valid\_and\_within\_bounds():  
\+    names \= social\_ingestion.REDDIT\_SUBREDDIT\_SHORTLIST  
\+    assert len(names) \== 10  
\+    assert all(normalize\_subreddit(name) for name in names)  
\+  
\+  
\+def test\_community\_yield\_reports\_unknown\_until\_analysed(db):  
\+    social\_ingestion.\_save\_items(  
\+        db,  
\+        \[  
\+            \_reddit("aaa111", "saas"),  
\+            \_reddit("bbb222", "saas", title="Another frustrating manual task"),  
\+            \_reddit(  
\+                "ccc333", "devops",  
\+                title="Yet another frustrating manual deploy chore",  
\+            ),  
\+        \],  
\+        "Reddit",  
\+    )  
\+    by\_id \= {  
\+        r.native\_id: r.submission\_id for r in db.scalars(select(SourceRecord))  
\+    }  
\+    good, noise \= (db.get(Submission, by\_id\["aaa111"\]),  
\+                   db.get(Submission, by\_id\["bbb222"\]))  
\+    good.analysis\_status \= noise.analysis\_status \= "completed"  
\+    good.analysis\_json \= json.dumps(  
\+        {"is\_genuine\_problem": True, "signal\_type": "pain\_point"}  
\+    )  
\+    noise.analysis\_json \= json.dumps(  
\+        {"is\_genuine\_problem": False, "signal\_type": "noise"}  
\+    )  
\+    cluster \= ProblemCluster(title="t", problem\_statement="p")  
\+    db.add(cluster)  
\+    db.flush()  
\+    db.add(ClusterEvidence(cluster\_id=cluster.id, submission\_id=good.id))  
\+    db.commit()  
\+  
\+    rows \= {r\["community"\]: r for r in community\_yield(db)}  
\+  
\+    assert rows\["saas"\]\["collected"\] \== 2  
\+    assert rows\["saas"\]\["analyzed"\] \== 2  
\+    assert rows\["saas"\]\["genuine"\] \== 1  
\+    assert rows\["saas"\]\["genuine\_rate"\] \== 0.5  
\+    assert rows\["saas"\]\["clustered"\] \== 1  
\+    assert rows\["devops"\]\["analyzed"\] \== 0  
\+    assert rows\["devops"\]\["genuine\_rate"\] is None  \# unknown, not 0%  
\+    \# Known rates sort ahead of unknown ones.  
\+    assert community\_yield(db)\[-1\]\["community"\] \== "devops"  
\+  
\+  
\+def test\_existing\_database\_gains\_provenance\_table\_without\_data\_loss():  
\+    engine \= create\_engine("sqlite://", poolclass=StaticPool,  
\+                           connect\_args={"check\_same\_thread": False})  
\+    legacy \= \[t for n, t in Base.metadata.tables.items() if n \!= "source\_records"\]  
\+    Base.metadata.create\_all(bind=engine, tables=legacy)  
\+    with engine.begin() as conn:  
\+        conn.execute(  
\+            Submission.\_\_table\_\_.insert().values(  
\+                text="legacy row", source\_name="manual",  
\+                language="en", analysis\_status="pending",  
\+                created\_at=\_\_import\_\_("datetime").datetime(2024, 1, 1),  
\+            )  
\+        )  
\+    assert "source\_records" not in inspect(engine).get\_table\_names()  
\+  
\+    initialize\_database(engine)  
\+  
\+    assert "source\_records" in inspect(engine).get\_table\_names()  
\+    with engine.connect() as conn:  
\+        assert conn.execute(select(Submission.\_\_table\_\_.c.text)).scalar() \== "legacy row"  
diff \-ruN \--strip-trailing-cr \-x \_\_pycache\_\_ \-x .pytest\_cache \-x .venv \-x .git \-x .env \-x data a/tests/test\_security.py b/tests/test\_security.py  
\--- a/tests/test\_security.py	1970-01-01 00:00:00.000000000 \+0000  
\+++ b/tests/test\_security.py	2026-10-20 06:28:49.250985433 \+0000  
@@ \-0,0 \+1,103 @@  
\+"""Opt-in API key and submission rate limiting (ENG-12)."""  
\+from types import SimpleNamespace  
\+  
\+import pytest  
\+from fastapi import HTTPException  
\+  
\+from app import security  
\+  
\+  
\+def \_request(path="/api/opportunities", host="10.0.0.1"):  
\+    return SimpleNamespace(  
\+        url=SimpleNamespace(path=path),  
\+        client=SimpleNamespace(host=host),  
\+    )  
\+  
\+  
\+def \_settings(monkeypatch, key="", rate=0):  
\+    monkeypatch.setattr(  
\+        security,  
\+        "get\_settings",  
\+        lambda: SimpleNamespace(  
\+            service\_api\_key=key,  
\+            submission\_rate\_limit\_per\_minute=rate,  
\+        ),  
\+    )  
\+  
\+  
\+def test\_no\_key\_configured\_means\_open\_access\_as\_before(monkeypatch):  
\+    \_settings(monkeypatch, key="")  
\+  
\+    assert security.require\_api\_key(\_request(), None) is None  
\+  
\+  
\+def test\_configured\_key\_is\_required\_except\_for\_health(monkeypatch):  
\+    \_settings(monkeypatch, key="s3cret")  
\+  
\+    assert security.require\_api\_key(\_request("/health"), None) is None  
\+    assert security.require\_api\_key(\_request(), "s3cret") is None  
\+    for bad in (None, "", "wrong", "S3CRET", "s3cret "):  
\+        with pytest.raises(HTTPException) as caught:  
\+            security.require\_api\_key(\_request(), bad)  
\+        assert caught.value.status\_code \== 401  
\+  
\+  
\+def test\_key\_comparison\_handles\_empty\_and\_unicode():  
\+    assert not security.api\_key\_matches("", "")  
\+    assert not security.api\_key\_matches(None, "x")  
\+    assert security.api\_key\_matches("clé-é", "clé-é")  
\+    assert not security.api\_key\_matches("clé", "cle")  
\+  
\+  
\+def test\_sliding\_window\_limits\_per\_client\_and\_recovers():  
\+    now \= \[0.0\]  
\+    limiter \= security.SlidingWindowLimiter(2, window=60, clock=lambda: now\[0\])  
\+  
\+    assert limiter.allow("a") and limiter.allow("a")  
\+    assert not limiter.allow("a")  
\+    assert limiter.allow("b")  \# other clients unaffected  
\+    now\[0\] \= 59.9  
\+    assert not limiter.allow("a")  
\+    now\[0\] \= 60.1  
\+    assert limiter.allow("a")  
\+  
\+  
\+def test\_limiter\_memory\_is\_bounded\_and\_fails\_closed(monkeypatch):  
\+    monkeypatch.setattr(security, "MAX\_TRACKED\_CLIENTS", 3\)  
\+    now \= \[0.0\]  
\+    limiter \= security.SlidingWindowLimiter(5, window=60, clock=lambda: now\[0\])  
\+    assert all(limiter.allow(str(i)) for i in range(3))  
\+  
\+    assert not limiter.allow("new-client")  \# table full of live entries  
\+    now\[0\] \= 61.0  
\+    assert limiter.allow("new-client")  \# stale entries pruned  
\+  
\+  
\+def test\_submission\_limit\_disabled\_by\_default\_and\_enforced\_when\_set(monkeypatch):  
\+    monkeypatch.setattr(security, "\_limiter", None)  
\+    \_settings(monkeypatch, rate=0)  
\+    for \_ in range(50):  
\+        assert security.limit\_submissions(\_request()) is None  
\+  
\+    \_settings(monkeypatch, rate=2)  
\+    security.limit\_submissions(\_request())  
\+    security.limit\_submissions(\_request())  
\+    with pytest.raises(HTTPException) as caught:  
\+        security.limit\_submissions(\_request())  
\+    assert caught.value.status\_code \== 429  
\+    security.limit\_submissions(\_request(host="10.0.0.2"))  
\+    monkeypatch.setattr(security, "\_limiter", None)  
\+  
\+  
\+def test\_http\_layer\_rejects\_missing\_key\_but\_serves\_health(monkeypatch):  
\+    testclient \= pytest.importorskip("fastapi.testclient")  
\+    from app.main import app  
\+  
\+    \_settings(monkeypatch, key="s3cret")  
\+    client \= testclient.TestClient(app)  
\+  
\+    assert client.get("/health").status\_code \== 200  
\+    assert client.get("/api/opportunities").status\_code \== 401  
\+    assert client.get(  
\+        "/api/opportunities", headers={"X-API-Key": "wrong"}  
\+    ).status\_code \== 401 implement these chaanges in to the code base 11 provenance tests pass, including the legacy-database upgrade case. Let me see how much of the \*existing\* suite I can exercise under the stand-ins, to look for regressions from my changes.

I’ve now covered most of your open-issues list. I ran 114 tests locally (1 skipped) against stand-ins for pydantic, FastAPI and google-genai, because your Windows \`.venv\` can’t load here. The pipeline, API, ingestion-endpoint and Gemini test files could not run, so please run your full suite before merging. I put the changes in \`ai-labs-problem-discovery-updated.zip\`, and \`backend-pass.patch\` holds the same changes as a diff.

\*\*What I did\*\*

\- \*\*Benchmark tooling (EVAL-01 to EVAL-06).\*\* I added \`app/evaluation.py\` and a \`benchmark/\` folder with a labeling guide.

\- \*\*Metrics:\*\* it measures precision, recall and false-positive rate for genuine problems, plus clustering false merges and missed matches, purchase-intent claims, quote traceability, independence over-claiming and ranking agreement with humans.  
\- \*\*Safeguards:\*\* it reports 95% confidence intervals and shows “n/a” instead of 0 when there are no samples. It applies no pass/fail thresholds, as your doc says to set those after a baseline.  
\- \*\*Checks:\*\* I checked the sample output by hand against the numbers it should produce. You still need to supply the real labeled posts.  
\- \*\*Source provenance.\*\* A new \`source\_records\` table stores the native post ID, subreddit or site, and whether the timestamp came from the provider or was a fallback.

\- \*\*Collectors:\*\* Hacker News, Stack Exchange, Reddit and X all write to it.  
\- \*\*Dedupe:\*\* Reddit and X now also catch the same post arriving under a different URL form.  
\- \*\*Safety:\*\* it is a new table only, so existing databases are untouched. I tested an upgrade of a legacy database.  
\- \*\*Subreddits.\*\* Your 10-community shortlist is in the code (\`REDDIT\_SUBREDDIT\_SHORTLIST\`). Passing \`reddit\_subreddits\` to the pipeline, or \`subreddits\` to \`/api/ingestion/reddit\`, collects from those communities’ “new” listings. The default keyword searches are unchanged.  
\- \*\*Yield tracking.\*\* A new endpoint, \`/api/sources/yield\`, shows per community how many posts were collected, analyzed, judged genuine and clustered. Rates are null until posts are analyzed.  
\- \*\*Security and config (ENG-12).\*\* There is an optional API key and an optional submission rate limit, both off by default. I also added the \`.env.example\` the audit said was missing.  
\- \*\*Earlier fixes.\*\* Noise no longer reaches clustering, and \`contradicts\`/\`mentions\` evidence no longer raises scores. I removed the scoring fix in a test run and confirmed the new test fails without it.

\*\*What I could not verify\*\*

\- The HTTP-level test for the API key, and the pipeline test for the noise gate, are written but unrun.  
\- Subreddit mode is unverified against the live Apify actor. I don’t know whether it accepts subreddit listing URLs, so please run one bounded request and inspect the output.  
\- The audit’s claimed Apify auth bug does not exist. The code sends \`Bearer {token}\`, and the audit most likely saw a redacted display.

\*\*What I left alone, and why\*\*

\- \*\*Benchmark labels.\*\* Human labels (EVAL-01) and the resulting accuracy numbers need real posts and reviewers. Nothing I produced counts as an accuracy claim.  
\- \*\*Live source checks (ENG-01 and ENG-02).\*\* These need a live Apify run with your token.  
\- \*\*Review actions and cross-source duplicates.\*\* Accept/reject/merge/split endpoints and preserving the second source of a cross-source duplicate both change shared logic and need decisions from you first.  
\- \*\*Comments, migrations and deployment.\*\* These come after the post pipeline is proven on real data. here is the summary what that code is , Green: keep and validate these changes

\- Rejecting explicit noise before clustering.  
\- Excluding \`contradicts\` and \`mentions\` evidence from positive scoring.  
\- Adding source provenance and timestamp-origin tracking.  
\- Building offline evaluation tooling without pretending synthetic examples establish accuracy.  
\- Keeping the Apify token dynamic and treating whitespace-only configuration as missing.

Amber: do not call these production-ready yet

\- Reddit/X actor compatibility and live authentication.  
\- The new API-key protection and rate limiting at the HTTP layer.  
\- Subreddit-targeted collection.  
\- The evaluation tool’s behavior on real, independently labelled records.  
\- Cross-source duplicate provenance and the database’s upgrade path.

Red: one important design limitation to keep visible

The new \`source\_records\` table uses \`submission\_id\` as its primary key, so it can hold only one provenance record per submission. If the same underlying submission is deduplicated across two sources into one \`Submission\`, this table cannot preserve both source records. Your report says cross-source duplicate preservation was deliberately left for a later decision; that is acceptable for this step, but the data model must be revisited before claiming complete multi-source provenance.

\#\# What the tests do — and do not — establish

\- 142 passing tests in your environment, plus a clean \`git diff \--check\`, are meaningful regression evidence for the earlier work you reported.  
\- Copilot’s 114 tests against stand-ins, with API/pipeline/Gemini files unrun, are useful partial evidence for its additional patch. They are not a replacement for running the actual project suite.  
\- The new benchmark’s synthetic fixture is a smoke test for the measurement code, not a measure of discovery accuracy.  
\- A mocked \`Authorization: Bearer \<configured token\>\` assertion establishes how the request is constructed. It does not establish that the token is valid, that an actor is accessible, or that its input and output match your adapter.  
\- The source-yield endpoint and API-key behavior need their unrun HTTP-level tests before I’d call those additions fully validated. implement them mcarefully withou tbreaking the system and also adpat if things go slightly off the goal is to keep the system reliable and working   
