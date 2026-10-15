def summarize(csv_text: str) -> dict[str, float]:
    lines = csv_text.strip().split("\n")
    totals: dict[str, float] = {}
    for line in lines[1:]:
        region, amount = line.split(",")
        totals[region] = totals.get(region, 0) + float(amount)
    return totals
