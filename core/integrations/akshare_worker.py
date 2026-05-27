from __future__ import annotations

import pickle
import sys
import traceback


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: python -m core.integrations.akshare_worker <func> <input.pkl> <output.pkl>", file=sys.stderr)
        return 2
    func_name, input_path, output_path = sys.argv[1:4]
    with open(input_path, "rb") as fp:
        args, kwargs = pickle.load(fp)
    try:
        import akshare as ak  # noqa: PLC0415

        func = getattr(ak, func_name, None)
        if func is None or not callable(func):
            raise AttributeError(f"akshare has no callable '{func_name}'")
        payload = ("ok", func(*args, **kwargs))
    except Exception as e:  # noqa: BLE001
        payload = ("err", (type(e).__name__, str(e), traceback.format_exc()))
    with open(output_path, "wb") as fp:
        pickle.dump(payload, fp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

