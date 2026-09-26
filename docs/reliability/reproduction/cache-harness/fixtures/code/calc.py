import json,sys
def net_total(rows):
    return sum(r["qty"]*r["unit_cents"] for r in rows)
if __name__ == "__main__":
    rows=json.load(open(sys.argv[1]))
    json.dump({"total_cents":net_total(rows)},open(sys.argv[2],"w"))
