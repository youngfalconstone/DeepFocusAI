"""Open the app on a given page with sample data and keep it open for N seconds (for screenshots).
Usage: python -m tests.show_page <dashboard|builder|history|settings> [seconds]"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepfocus import catalog  # noqa: E402
from deepfocus.app import App  # noqa: E402

page = sys.argv[1] if len(sys.argv) > 1 else "dashboard"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 6

app = App()
app.geometry("1160x780+40+40")


def setup():
    if page == "builder":
        b = app.pages["builder"]
        b.goal_var.set("Finish the quarterly report draft")
        b.add_service(catalog.get("youtube"))
        b.add_service(catalog.get("discord"))
        b.add_service(catalog.get("reddit"))
        b.search.delete(0, "end"); b.search._ph = False; b.search.insert(0, "twit"); b.find()
    app.show(page)
    app.after(int(secs * 1000), app.destroy)


app.after(600, setup)
app.mainloop()
