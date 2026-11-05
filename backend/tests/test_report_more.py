from app.reporter import _more_sections

TAIL = ["<details>", "<summary>Evidence (2 findings)</summary>", "", "body A", "<details>", "<summary>inner</summary>", "x", "</details>", "", "</details>", "",
        "<details>", "<summary>How the data was set up (business, sources, cleaning)</summary>", "", "method", "</details>", "",
        "<details>", "<summary>Statistics and limitations</summary>", "", "limits", "</details>", "",
        "<details>", "<summary>Power BI</summary>", "", "pack", "</details>"]


def test_method_sections_stay_out_and_content_stays_in():
    out = _more_sections(TAIL)
    assert [s["title"] for s in out] == ["Evidence", "Limits and data gaps"]
    assert "inner" in out[0]["md"] and "method" not in "".join(s["md"] for s in out)
