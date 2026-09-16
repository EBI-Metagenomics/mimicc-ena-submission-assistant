"""Study/sample submit and list, the generic records browser/actions, and reads suggest.

Stateless: Webin credentials arrive per-request (see ``webin_creds``). Reads
grouping, the upload plan and helper outcomes, and study/sample prepare, run in
the browser instead (``py()`` → ``ena_service`` / ``read_assign``).
"""

from __future__ import annotations

import json
from typing import Any

import ena_service
import read_assign
import webin_creds
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from pydantic import BaseModel, ValidationError

# Submission alias for MODIFYs from this app, so they are identifiable in ENA.
MODIFY_ALIAS = "mimicc-assistant-modify"


def _list_params(request: HttpRequest) -> dict[str, Any]:
    """The query string of a record listing, as ``list_records`` kwargs.

    ``full_fields`` is off by default: it costs a Browser API request per 100
    records (and, on production, a Portal request per 50), which is only worth
    paying when someone actually wants the checklist columns.
    """
    return {
        "test": request.GET.get("test", "true").lower() != "false",
        "status": request.GET.get("status", "all"),
        "search": request.GET.get("search", "").strip(),
        "linked_to": request.GET.get("linked_to", "").strip(),
        "unlinked": request.GET.get("unlinked") == "true",
        "full_fields": request.GET.get("full_fields", "false").lower() == "true",
        "max_results": int(request.GET.get("max_results", 5000)),
    }


class StudySubmitRequest(BaseModel):
    records: list[dict[str, Any]]
    test: bool = True
    modify: bool = False
    hold_until: str | None = None
    public: bool = False


class SampleSubmitRequest(BaseModel):
    records: list[dict[str, Any]]
    test: bool = True
    modify: bool = False
    checklist: str | None = "ERC000025"
    hold_until: str | None = None
    public: bool = False


class ActionRequest(BaseModel):
    action: str
    accession: str
    test: bool = True
    alias: str | None = None
    hold_until: str | None = None


class FieldsRequest(BaseModel):
    accessions: list[str]
    test: bool = True


class ModifyRecord(BaseModel):
    accession: str
    changes: dict[str, Any]


class ModifyRequest(BaseModel):
    entity: str
    records: list[ModifyRecord]
    test: bool = True


class SuggestRequest(BaseModel):
    groups: list[dict[str, Any]]
    test: bool = True
    max_results: int = 5000


def _parse(model, request: HttpRequest):
    return model.model_validate(json.loads(request.body))


# ---------------------------------------------------------------------------
# Studies
# ---------------------------------------------------------------------------


def study_submit(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(StudySubmitRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    try:
        return JsonResponse(
            ena_service.submit_studies(
                creds, req.records, test=req.test, modify=req.modify, hold_until=req.hold_until, public=req.public
            )
        )
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


def study_list(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    return JsonResponse(ena_service.list_records(creds, "studies", **_list_params(request)), safe=False)


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------


def sample_submit(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(SampleSubmitRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    try:
        return JsonResponse(
            ena_service.submit_samples(
                creds,
                req.records,
                test=req.test,
                modify=req.modify,
                checklist=req.checklist,
                hold_until=req.hold_until,
                public=req.public,
            )
        )
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


def sample_list(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    return JsonResponse(ena_service.list_records(creds, "samples", **_list_params(request)), safe=False)


# ---------------------------------------------------------------------------
# Records browser + lifecycle actions
# ---------------------------------------------------------------------------


def records_list(request: HttpRequest, entity: str) -> JsonResponse:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        return JsonResponse(ena_service.list_records(creds, entity, **_list_params(request)), safe=False)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


def records_action(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(ActionRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    try:
        return JsonResponse(
            ena_service.run_action(
                creds, req.action, req.accession, test=req.test, alias=req.alias, hold_until=req.hold_until
            )
        )
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


def records_fields(request: HttpRequest, entity: str) -> JsonResponse:
    """Current values of the editable fields for the given accessions."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(FieldsRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    try:
        fields = ena_service.read_editable_fields(creds, entity, req.accessions, test=req.test)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001 - surface it; a Django 500 page shows the UI nothing
        return JsonResponse({"detail": f"{type(exc).__name__}: {exc}"}, status=502)
    return JsonResponse({"fields": fields})


def _modify(request: HttpRequest, *, submit: bool) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(ModifyRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    records = [r.model_dump() for r in req.records]
    call = ena_service.modify_records if submit else ena_service.preview_modify_records
    try:
        result = call(creds, req.entity, records, test=req.test, submission_alias=MODIFY_ALIAS)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001 - surface it; a Django 500 page shows the UI nothing
        return JsonResponse({"detail": f"{type(exc).__name__}: {exc}"}, status=502)
    return JsonResponse(result)


def records_modify_preview(request: HttpRequest) -> JsonResponse:
    return _modify(request, submit=False)


def records_modify(request: HttpRequest) -> JsonResponse:
    # This app exists to submit. Write mode is an explicit per-session opt-in in the UI,
    # and lifecycle actions keep confirming as they already do.
    return _modify(request, submit=True)


# ---------------------------------------------------------------------------
# Reads: suggest
# ---------------------------------------------------------------------------


def reads_suggest(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(SuggestRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    samples = ena_service.list_records(creds, "samples", test=req.test, max_results=req.max_results)
    return JsonResponse({"groups": read_assign.suggest(req.groups, samples), "samples": samples})
