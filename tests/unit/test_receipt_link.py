"""Receipt links (AGENTS.md 15.8): the whole receipt rides in the URL fragment, nothing is stored."""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.verify.receipt import (
    LINK_PREFIX,
    RECEIPT_VIEWER_URL,
    describe,
    is_receipt_link,
    receipt_from_link,
    receipt_link,
)
from qedcode.verify.verify import verify_receipt
from tests.unit.test_receipt_verify import graded_a, make_receipt

runner = CliRunner()


@pytest.fixture(scope="module")
def proven(tmp_path_factory):
    from qedcode.verify.receipt import save_receipt

    tmp = tmp_path_factory.mktemp("linked")
    case, cand = graded_a(tmp)
    receipt = make_receipt(case, cand, model_info={"model": "fake:scripted", "temperatures": [0.2]})
    return case, receipt, save_receipt(case.project, receipt)


def test_a_link_round_trips_and_is_always_the_same(proven) -> None:
    _, receipt, _ = proven
    link = receipt_link(receipt)
    assert link.startswith(RECEIPT_VIEWER_URL + LINK_PREFIX)
    assert receipt_link(receipt) == link  # gzip mtime 0: the same receipt, the same link
    assert receipt_from_link(link) == receipt
    assert is_receipt_link(link) and not is_receipt_link("receipt.json")
    # only URL-safe characters after the '#', so chat apps and PR comments keep it whole
    data = link.split(LINK_PREFIX, 1)[1]
    assert set(data) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_a_self_hosted_viewer_can_be_used(proven) -> None:
    _, receipt, _ = proven
    link = receipt_link(receipt, "https://example.org/viewer/#old")
    assert link.startswith("https://example.org/viewer/#r=")


def test_a_link_verifies_like_the_file(proven) -> None:
    case, receipt, _ = proven
    result = verify_receipt(receipt_link(receipt), case.project)
    assert result.verified, result.mismatches


def test_an_edited_patch_inside_a_link_is_caught(proven) -> None:
    case, receipt, _ = proven
    data = json.loads(receipt.model_dump_json())
    data["patch"] = data["patch"].replace("start + size", "start + size + 1")
    packed = base64.urlsafe_b64encode(gzip.compress(json.dumps(data).encode(), mtime=0))
    link = RECEIPT_VIEWER_URL + LINK_PREFIX + packed.decode().rstrip("=")
    result = verify_receipt(link, case.project)
    assert not result.verified
    assert "checksum" in result.mismatches[0]


def test_damaged_links_get_plain_errors() -> None:
    with pytest.raises(ValueError, match="no receipt"):
        receipt_from_link(RECEIPT_VIEWER_URL)
    with pytest.raises(ValueError, match="damaged"):
        receipt_from_link(RECEIPT_VIEWER_URL + "#r=not-really-base64!!")
    not_a_receipt = base64.urlsafe_b64encode(gzip.compress(b'{"hello": 1}')).decode()
    with pytest.raises(ValueError, match="valid QEDCode receipt"):
        receipt_from_link(RECEIPT_VIEWER_URL + "#r=" + not_a_receipt)


def test_a_huge_receipt_is_refused_with_a_way_out(proven) -> None:
    _, receipt, _ = proven
    import os

    big = receipt.model_copy(update={"patch": base64.b64encode(os.urandom(80_000)).decode()})
    with pytest.raises(ValueError, match="Share the receipt file"):
        receipt_link(big)


def test_describe_names_the_proof_and_the_gaps(proven) -> None:
    _, receipt, _ = proven
    text = "\n".join(describe(receipt))
    assert text.startswith("Grade A")
    assert "Checks that ran:" in text and "fbpa: ok" in text
    assert "Not proven:" in text and receipt.patch_sha256 in text
    assert "temperatures 0.2" in text


def test_qed_receipt_shows_the_newest_receipt(proven, monkeypatch) -> None:
    case, receipt, path = proven
    monkeypatch.chdir(case.project)
    result = runner.invoke(app, ["receipt"])
    assert result.exit_code == 0, result.output
    assert "Grade A" in result.stdout and path.name in result.stdout.replace("\n", "")


def test_qed_receipt_link_warns_that_the_diff_is_inside(proven, monkeypatch) -> None:
    case, receipt, path = proven
    monkeypatch.chdir(case.project)
    result = runner.invoke(app, ["receipt", str(path), "--link"])
    assert result.exit_code == 0
    assert result.stdout.strip() == receipt_link(receipt)
    assert "contains the whole receipt, including the diff" in result.stderr


def test_qed_receipt_json_and_errors(proven, tmp_path: Path, monkeypatch) -> None:
    _, _, path = proven
    shown = runner.invoke(app, ["receipt", str(path), "--json"])
    assert json.loads(shown.stdout)["grade"] == "A"
    monkeypatch.chdir(tmp_path)
    empty = runner.invoke(app, ["receipt"])
    assert empty.exit_code == 2 and "no receipt here yet" in empty.output


def test_qed_verify_accepts_a_link(proven, monkeypatch) -> None:
    case, receipt, _ = proven
    monkeypatch.chdir(case.project)
    result = runner.invoke(app, ["verify", receipt_link(receipt)])
    assert result.exit_code == 0, result.output
    assert "VERIFIED" in result.output
