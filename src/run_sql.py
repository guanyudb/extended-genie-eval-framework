"""Run SQL files from this folder on the configured warehouse, filling in {{schema}} and {{model}} from common.py.

  python run_sql.py 01a_synthetic_data.sql 01b_synthetic_data.sql   # run in order
  python run_sql.py --print 03a_generate_variations.sql             # show the SQL without running it
"""
import pathlib
import sys
import time

from common import MODEL, SCHEMA, sql


def render(name: str) -> str:
    return pathlib.Path(name).read_text().replace("{{schema}}", SCHEMA).replace("{{model}}", MODEL)


def main(args: list[str]) -> None:
    show = "--print" in args
    for name in [a for a in args if a != "--print"]:
        if show:
            print(f"-- {name}\n{render(name)}\n")
            continue
        t0 = time.time()
        rows = sql(render(name))
        print(f"{name}: done in {time.time() - t0:.0f}s" + (f", {len(rows)} rows" if rows else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
