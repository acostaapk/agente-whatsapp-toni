def recommend_model(
    *,
    explicit_model: str | None,
    needs_print: bool | None,
    wants_touchscreen_or_4g: bool,
    needs_pix: bool,
    lowest_investment: bool,
    catalog_ids: set[str],
) -> str | None:
    def compatible(model_id: str) -> bool:
        if needs_print and model_id not in {"t3", "t3_smart"}:
            return False
        if needs_pix and model_id == "t1":
            return False
        if wants_touchscreen_or_4g and model_id != "t3_smart":
            return False
        return True

    if explicit_model in catalog_ids and compatible(explicit_model):
        return explicit_model

    if wants_touchscreen_or_4g:
        return "t3_smart" if "t3_smart" in catalog_ids else None
    if needs_print and "t3" in catalog_ids:
        return "t3"
    if needs_print and "t3_smart" in catalog_ids:
        return "t3_smart"
    if needs_pix and "t2" in catalog_ids:
        return "t2"
    if lowest_investment and not needs_pix and "t1" in catalog_ids:
        return "t1"
    if (needs_pix or needs_print is False) and "t2" in catalog_ids:
        return "t2"
    return None


def recommend_for_existing_machine(
    *,
    needs_print: bool | None,
    wants_touchscreen_or_4g: bool,
    needs_pix: bool,
    lowest_investment: bool,
    catalog_ids: set[str],
) -> str | None:
    """Resume a change-machine conversation only from needs the customer stated."""
    return recommend_model(
        explicit_model=None,
        needs_print=needs_print,
        wants_touchscreen_or_4g=wants_touchscreen_or_4g,
        needs_pix=needs_pix,
        lowest_investment=lowest_investment,
        catalog_ids=catalog_ids,
    )


def same_cnpj_ton_restricted(*, selected_document: str, has_active_ton: bool) -> bool:
    return selected_document.lower() == "cnpj" and has_active_ton
