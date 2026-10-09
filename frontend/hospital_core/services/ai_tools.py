"""Rule-based NLP helpers: patient request classification and referral summarization.
Pure Python - no external API or model download needed, so it works offline in a demo.
"""
import re

# (regex, weight). Regexes use word starts so "difficult" never matches "icu".
RESOURCE_RULES = {
    "icu_bed": [
        (r"\bicu\b", 3), (r"intensive care", 3), (r"\bunconscious", 2), (r"\bunresponsive", 2),
        (r"\bventilat", 3), (r"\bintubat", 3), (r"respiratory failure", 3), (r"cardiac arrest", 3),
        (r"\bsepsis|\bseptic", 2), (r"\bshock\b", 2), (r"\bcoma\b", 3), (r"not breathing", 3),
        (r"multi-?organ", 3), (r"\bgcs\s*[:=]?\s*[3-8]\b", 3),
    ],
    "nicu_bed": [
        (r"\bnewborn", 3), (r"\bneonat", 3), (r"\bpremature", 3), (r"\bpreterm", 3),
        (r"\b\d+\s*days?\s*old", 2), (r"\bnicu\b", 3), (r"\bincubator", 3),
    ],
    "trauma": [
        (r"\baccident", 2), (r"road traffic", 3), (r"\brta\b", 3), (r"\bfracture", 2),
        (r"\bgunshot", 3), (r"\bstab(bed|bing| wound)", 3), (r"head injury", 3),
        (r"\bfell from|\bfall from", 2), (r"\bburns?\b", 2), (r"\bbleeding", 2), (r"\bcrush", 3),
        (r"\btrauma", 3),
    ],
    "dialysis": [
        (r"\bdialysis", 4), (r"kidney failure", 3), (r"renal failure", 3), (r"\bcreatinine", 2),
        (r"\bckd\b", 2), (r"hyperkal[a]?emia", 2),
    ],
    "operation_theatre": [
        (r"\bsurgery|\bsurgical", 3), (r"\boperation\b", 2), (r"\bappendicitis", 3),
        (r"\bcaesarean|\bcesarean|c-section", 3), (r"\bperforat", 3), (r"\bobstruct", 2),
        (r"\bhernia", 2), (r"\blaparotomy", 3),
    ],
    "isolation_bed": [
        (r"\bcovid", 3), (r"\btuberculosis|\btb\b", 3), (r"\binfectious", 3), (r"\bisolation", 3),
        (r"\bmeningitis", 3), (r"\bcontagious", 3), (r"\bcholera", 2),
    ],
    "emergency_bed": [
        (r"chest pain", 3), (r"\bseizure", 2), (r"\bstroke", 3), (r"heart attack", 3),
        (r"difficulty (in )?breathing", 2), (r"shortness of breath", 2), (r"\bpoison", 3),
        (r"\boverdose", 3), (r"\ballergic", 2), (r"\banaphyla", 3), (r"severe pain", 2),
        (r"high fever", 1), (r"vomiting blood", 3), (r"\bfits\b", 2),
    ],
}
PRIORITY = ["icu_bed", "nicu_bed", "trauma", "dialysis", "operation_theatre",
            "isolation_bed", "emergency_bed", "general_bed"]

VENT_PATTERNS = [r"\bventilat", r"\bintubat", r"respiratory failure", r"not breathing",
                 r"cardiac arrest", r"on oxygen support and failing"]

CRITICAL_PATTERNS = [r"\bunconscious", r"\bunresponsive", r"cardiac arrest", r"not breathing",
                     r"severe bleeding", r"\bgcs\s*[:=]?\s*[3-8]\b", r"\bshock\b", r"\bcoma\b",
                     r"respiratory failure", r"anaphyla"]
HIGH_PATTERNS = [r"chest pain", r"\bstroke", r"\bseizure", r"road traffic", r"\baccident",
                 r"difficulty (in )?breathing", r"shortness of breath", r"\bbleeding", r"\bsepsis|\bseptic",
                 r"\bgunshot", r"\bstab(bed|bing| wound)", r"head injury", r"heart attack", r"\boverdose"]
LOW_PATTERNS = [r"\broutine", r"follow[- ]?up", r"\bmild\b", r"\bstable\b", r"check[- ]?up"]

SERVICE_RULES = {
    "Cardiology": [r"chest pain", r"\bheart", r"\bcardiac", r"myocardial", r"\bangina", r"\bpalpitation"],
    "Neurology": [r"\bstroke", r"\bseizure", r"head injury", r"\bparalysis", r"\bunconscious", r"\bfits\b"],
    "Pediatrics": [r"\bchild", r"\binfant", r"\bnewborn", r"\bbaby", r"\btoddler"],
    "Orthopedics": [r"\bfracture", r"\bbone", r"\bjoint", r"\bdislocat"],
    "Dialysis": [r"\bdialysis", r"kidney", r"\brenal"],
    "Obstetrics": [r"\bpregnan", r"\blabou?r\b", r"\bdelivery", r"\bantenatal", r"\bcaesarean|c-section"],
}

CONDITIONS = [
    (r"\bdiabet", "diabetes"), (r"\bhypertens|high blood pressure", "hypertension"),
    (r"\basthma", "asthma"), (r"\bcopd\b", "COPD"), (r"\bckd\b|kidney disease", "kidney disease"),
    (r"heart failure", "heart failure"), (r"\bcoronary|\bmi\b|heart attack", "coronary disease"),
    (r"\bcancer", "cancer"), (r"\bepilep", "epilepsy"), (r"\bpregnan", "pregnancy"),
    (r"\bstroke", "stroke"), (r"\bcovid", "COVID-19"),
]


def _hits(text, patterns):
    return [p for p in patterns if re.search(p, text)]


def _num(m, cast=int):
    try:
        return cast(m.group(1))
    except (AttributeError, ValueError, IndexError):
        return None


def extract_vitals(text: str) -> dict:
    t = text.lower()
    vitals = {}
    m = re.search(r"\b(?:bp|blood pressure)\D{0,6}(\d{2,3})\s*/\s*(\d{2,3})", t)
    if not m:
        m = re.search(r"\b(\d{2,3})\s*/\s*(\d{2,3})\s*mm\s*hg", t)
    if m:
        vitals["bp"] = f"{m.group(1)}/{m.group(2)}"
        vitals["systolic"] = int(m.group(1))
    hr = _num(re.search(r"\b(?:hr|heart rate|pulse)\D{0,8}(\d{2,3})", t))
    if hr:
        vitals["hr"] = hr
    spo2 = _num(re.search(r"\b(?:spo2|sp02|o2 sat(?:uration)?s?|oxygen saturation|saturation)\D{0,10}(\d{2,3})", t))
    if spo2 and 30 <= spo2 <= 100:
        vitals["spo2"] = spo2
    temp = _num(re.search(r"\btemp(?:erature)?\D{0,8}(\d{2,3}(?:\.\d)?)", t), float)
    if temp:
        vitals["temp"] = temp
    rr = _num(re.search(r"\b(?:rr|resp(?:iratory)? rate)\D{0,8}(\d{1,2})\b", t))
    if rr:
        vitals["rr"] = rr
    return vitals


def classify(text: str) -> dict:
    """Which facility does this patient need, how urgent is it, which specialist?"""
    t = text.lower()
    vitals = extract_vitals(text)
    scores, matched = {}, {}
    for res, rules in RESOURCE_RULES.items():
        s = 0
        for pat, w in rules:
            if re.search(pat, t):
                s += w
                matched.setdefault(res, []).append(re.sub(r"\\b|\\s\*|\(.*?\)|[\\?|]", " ", pat).strip()[:25])
        if s:
            scores[res] = s

    spo2 = vitals.get("spo2")
    if spo2 is not None and spo2 < 90:
        scores["icu_bed"] = scores.get("icu_bed", 0) + 3
        matched.setdefault("icu_bed", []).append(f"SpO2 {spo2}%")
    if vitals.get("systolic") and vitals["systolic"] < 90:
        scores["icu_bed"] = scores.get("icu_bed", 0) + 2
        matched.setdefault("icu_bed", []).append(f"low BP {vitals['bp']}")

    if scores:
        best = max(scores, key=lambda r: (scores[r], -PRIORITY.index(r)))
        ordered = sorted(scores.values(), reverse=True)
        second = ordered[1] if len(ordered) > 1 else 0
        confidence = round(100 * ordered[0] / (ordered[0] + second + 2))
    else:
        best, confidence = "general_bed", 40

    needs_vent = bool(_hits(t, VENT_PATTERNS)) or (spo2 is not None and spo2 < 85)

    urgency = "medium"
    if _hits(t, CRITICAL_PATTERNS) or (spo2 is not None and spo2 < 90) or \
            (vitals.get("systolic") and vitals["systolic"] < 90):
        urgency = "critical"
    elif _hits(t, HIGH_PATTERNS) or (vitals.get("hr") and (vitals["hr"] > 130 or vitals["hr"] < 40)):
        urgency = "high"
    elif _hits(t, LOW_PATTERNS) and best == "general_bed":
        urgency = "low"

    if urgency in ("low", "medium") and (needs_vent or best == "icu_bed"):
        urgency = "high"          # anyone who needs intensive care or a ventilator is at least high urgency

    service, best_svc = None, 0
    for name, pats in SERVICE_RULES.items():
        n = len(_hits(t, pats))
        if n > best_svc:
            service, best_svc = name, n

    return {"resource_type": best, "needs_ventilator": needs_vent, "urgency": urgency,
            "service": service, "confidence": confidence,
            "matched": matched.get(best, [])[:5], "vitals": vitals}


def _age(t):
    m = re.search(r"\b(\d{1,3})\s*[- ]?\s*(?:years?|yrs?|y/o|yo)\b(?:[- ]old)?", t)
    if m:
        return f"{m.group(1)} y/o"
    m = re.search(r"\baged?\s*(\d{1,3})\b", t)
    if m:
        return f"{m.group(1)} y/o"
    m = re.search(r"\b(\d{1,2})\s*[- ]?\s*months?\b", t)
    if m:
        return f"{m.group(1)} months"
    m = re.search(r"\b(\d{1,3})\s*[- ]?\s*days?\s*old", t)
    if m:
        return f"{m.group(1)} days"
    return None


def _sex(t):
    m = re.search(r"\b(male|female|man|woman|boy|girl)\b", t)
    if m:
        return "female" if m.group(1) in ("female", "woman", "girl") else "male"
    if re.search(r"\bshe\b|\bher\b", t):
        return "female"
    if re.search(r"\bhe\b|\bhis\b", t):
        return "male"
    return None


def summarize(text: str) -> dict:
    """Turn a long case description into short structured information."""
    t = text.lower()
    cls = classify(text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", text.strip()) if s.strip()]
    all_pats = [p for rules in RESOURCE_RULES.values() for p, _ in rules]

    def score(s):
        sl = s.lower()
        return sum(1 for p in all_pats if re.search(p, sl))

    ranked = sorted(sentences, key=score, reverse=True)
    complaint = (ranked[0] if ranked and score(ranked[0]) else (sentences[0] if sentences else text))[:160]
    key_sentences = [s[:160] for s in ranked[:2] if score(s)]

    history = [label for pat, label in CONDITIONS if re.search(pat, t)]
    vit = cls["vitals"]
    vit_txt = ", ".join(x for x in [
        f"BP {vit['bp']}" if "bp" in vit else None,
        f"HR {vit['hr']}" if "hr" in vit else None,
        f"SpO2 {vit['spo2']}%" if "spo2" in vit else None,
        f"Temp {vit['temp']}" if "temp" in vit else None,
        f"RR {vit['rr']}" if "rr" in vit else None] if x)

    age, sex = _age(t), _sex(t)
    need = cls["resource_type"].replace("_", " ") + (" + ventilator" if cls["needs_ventilator"] else "")
    parts = [" ".join(x for x in [age, sex] if x) or None,
             f"complaint: {complaint.rstrip('.')}",
             vit_txt or None,
             f"history: {', '.join(history)}" if history else None,
             f"needs: {need}", f"urgency: {cls['urgency']}"]
    return {"age": age, "sex": sex, "chief_complaint": complaint, "vitals": vit,
            "history": history, "key_sentences": key_sentences,
            "needs": need, "urgency": cls["urgency"], "service": cls["service"],
            "summary_text": " | ".join(p for p in parts if p)[:900]}
