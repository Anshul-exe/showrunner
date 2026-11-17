import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "ci"))
import approve  # noqa: E402

APP = 5807966


def test_parse_command():
    assert approve.parse_command("/approve A1") == ("approve", "A1", "")
    assert approve.parse_command("  /Reject a2 vendor already recovered @someone\nmore") == \
        ("reject", "A2", "vendor already recovered (at)someone")
    assert approve.parse_command("I /approve A1") is None
    assert approve.parse_command("/approve all") is None
    assert approve.parse_command("") is None


def test_codeowners():
    assert approve.codeowners("# c\n* @Owner-One @org/team\nops/ @two # x\n") == {"owner-one", "two"}


def test_token_only_from_the_app_and_newest_wins():
    comments = [
        {"performed_via_github_app": {"id": APP}, "body": "x <!-- sr-task-token A1 OLD -->"},
        {"performed_via_github_app": None, "body": "<!-- sr-task-token A1 FORGED -->", "user": {"login": "someone"}},
        {"performed_via_github_app": {"id": 1}, "body": "<!-- sr-task-token A1 OTHERAPP -->"},
        {"performed_via_github_app": {"id": APP}, "body": "analysis\n<!-- sr-task-token A1 AAAA+/=_-.:Z -->"},
    ]
    assert approve.find_token(comments, "A1", APP) == "AAAA+/=_-.:Z"
    assert approve.find_token(comments, "A2", APP) is None
    assert approve.find_token(comments[1:3], "A1", APP) is None
