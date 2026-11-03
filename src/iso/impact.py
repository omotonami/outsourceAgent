"""AI system impact assessment (ISO/IEC 42005, cho clause 6.1.4/8.4 của 42001).
Triage trước (quyết định có cần đánh giá đầy đủ), rồi sinh BẢN NHÁP từ Blackboard.
Nhìn RA NGOÀI: tác động tới cá nhân/nhóm/xã hội. Người chỉ duyệt phần phán đoán."""

HIGH_FLAGS = {"PDPA", "MAS TRM", "financial", "personal_data", "health", "biometric"}

def triage(compliance_flags, processes_personal_data=True):
    flags = set(compliance_flags or [])
    high = bool(flags & HIGH_FLAGS) or processes_personal_data
    return {"level": "high" if high else "low",
            "full_required": high,
            "reasons": sorted(flags & HIGH_FLAGS) + (["xử lý dữ liệu cá nhân"] if processes_personal_data else [])}

def draft(business_context, compliance_flags):
    tri = triage(compliance_flags)
    areas = ["privacy", "fairness", "safety", "human_autonomy", "societal"]
    return {
        "scope": business_context.get("domain", "?") + " — " + ", ".join(m.get("name","") for m in business_context.get("modules", []))[:120],
        "triage": tri,
        "affected_parties": ["khách hàng cuối", "nhân viên vận hành", "bên thứ ba (FX)"],
        "impacts": [{"area": a, "potential": "<cần người đánh giá>", "mitigation": "<cần người>", "needs_human": True} for a in areas],
        "data_governance": {"pdpa": "PDPA" in (compliance_flags or []), "pii_masked": True, "residency": "<chốt theo hợp đồng>"},
        "residual_risk": "<điền sau khi có mitigation>",
        "standard": "ISO/IEC 42005:2025 (hướng dẫn) cho 42001 cl.6.1.4/8.4",
    }
