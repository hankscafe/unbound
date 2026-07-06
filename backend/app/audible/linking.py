"""Multi-step Audible account linking.

The ``audible`` library authenticates via a single blocking call with *callbacks*
(OTP, CAPTCHA, CVF, push-approval, external login URL). A web app cannot block, so
each link attempt runs in a background thread and the callbacks hand control back to
the API through queues: the thread announces which prompt it needs, the HTTP layer
collects that input from the user, and the thread resumes.

Both supported flows share this machinery:
- **guided**: ``Authenticator.from_login`` with OTP/CAPTCHA/CVF/approval callbacks.
- **external**: ``Authenticator.from_login_external`` with a login-URL callback; the
  user logs in via their own browser and pastes back the response URL.

Flow state is in-memory (single-instance self-hosted app). A completed flow yields a
serialized auth blob that the caller encrypts and stores on the ``AudibleAccount``.
"""

from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.audible import client as ac
from app.core.logging import get_logger

log = get_logger("audible.linking")

# Prompt kinds surfaced to the API / UI.
NEEDS_OTP = "needs_otp"
NEEDS_CAPTCHA = "needs_captcha"
NEEDS_CVF = "needs_cvf"
NEEDS_APPROVAL = "needs_approval"
NEEDS_RESPONSE_URL = "needs_response_url"
LINKED = "linked"
ERROR = "error"

_WAIT_TIMEOUT = 90.0  # seconds to wait for the worker thread to reach the next step


@dataclass
class Step:
    status: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Flow:
    id: str
    marketplace: str
    kind: str  # "guided" | "external"
    thread: threading.Thread | None = None
    events: "queue.Queue[Step]" = field(default_factory=queue.Queue)
    inputs: dict[str, "queue.Queue[str]"] = field(default_factory=dict)
    result_blob: str | None = None
    error: str | None = None
    done: bool = False

    def input_queue(self, kind: str) -> "queue.Queue[str]":
        return self.inputs.setdefault(kind, queue.Queue(maxsize=1))


class LinkFlowManager:
    """Owns in-progress link flows for the process."""

    def __init__(self) -> None:
        self._flows: dict[str, _Flow] = {}
        self._lock = threading.Lock()

    # -- public API ---------------------------------------------------------

    def start_guided(self, *, email: str, password: str, marketplace: str) -> tuple[str, Step]:
        ac._require_audible()
        flow = _Flow(id=uuid.uuid4().hex, marketplace=marketplace, kind="guided")
        with self._lock:
            self._flows[flow.id] = flow
        flow.thread = threading.Thread(
            target=self._run_guided, args=(flow, email, password), daemon=True
        )
        flow.thread.start()
        return flow.id, self._await_step(flow)

    def start_external(self, *, marketplace: str) -> tuple[str, Step]:
        ac._require_audible()
        flow = _Flow(id=uuid.uuid4().hex, marketplace=marketplace, kind="external")
        with self._lock:
            self._flows[flow.id] = flow
        flow.thread = threading.Thread(target=self._run_external, args=(flow,), daemon=True)
        flow.thread.start()
        return flow.id, self._await_step(flow)

    def provide(self, flow_id: str, kind: str, value: str) -> Step:
        flow = self._get(flow_id)
        flow.input_queue(kind).put(value)
        return self._await_step(flow)

    def take_result(self, flow_id: str) -> str | None:
        """Return and consume the serialized auth blob for a linked flow."""
        flow = self._get(flow_id)
        blob = flow.result_blob
        if flow.done:
            with self._lock:
                self._flows.pop(flow_id, None)
        return blob

    def discard(self, flow_id: str) -> None:
        with self._lock:
            self._flows.pop(flow_id, None)

    # -- internals ----------------------------------------------------------

    def _get(self, flow_id: str) -> _Flow:
        with self._lock:
            flow = self._flows.get(flow_id)
        if flow is None:
            raise KeyError(f"Unknown link flow: {flow_id}")
        return flow

    def _await_step(self, flow: _Flow) -> Step:
        try:
            step = flow.events.get(timeout=_WAIT_TIMEOUT)
        except queue.Empty:
            return Step(ERROR, {"message": "Timed out waiting for Audible"})
        if step.status in (LINKED, ERROR):
            flow.done = True
        return step

    def _make_callbacks(self, flow: _Flow) -> dict[str, Any]:
        def otp_callback() -> str:
            flow.events.put(Step(NEEDS_OTP, {"flow_id": flow.id}))
            return flow.input_queue("otp").get()

        def captcha_callback(captcha_url: str) -> str:
            flow.events.put(
                Step(NEEDS_CAPTCHA, {"flow_id": flow.id, "captcha_image_url": captcha_url})
            )
            return flow.input_queue("captcha").get()

        def cvf_callback() -> str:
            flow.events.put(Step(NEEDS_CVF, {"flow_id": flow.id}))
            return flow.input_queue("cvf").get()

        def approval_callback() -> None:
            # Amazon "approve sign-in" push. Ask the user to approve, then continue.
            flow.events.put(Step(NEEDS_APPROVAL, {"flow_id": flow.id}))
            flow.input_queue("approval").get()
            return None

        return {
            "otp_callback": otp_callback,
            "captcha_callback": captcha_callback,
            "cvf_callback": cvf_callback,
            "approval_callback": approval_callback,
        }

    def _run_guided(self, flow: _Flow, email: str, password: str) -> None:
        try:
            auth = ac.audible.Authenticator.from_login(  # type: ignore[union-attr]
                email,
                password,
                locale=flow.marketplace,
                with_username=False,
                **self._make_callbacks(flow),
            )
            self._finish(flow, auth)
        except Exception as exc:  # pragma: no cover - network path
            log.warning("guided_link_failed", error=str(exc))
            flow.error = str(exc)
            flow.events.put(Step(ERROR, {"message": str(exc)}))

    def _run_external(self, flow: _Flow) -> None:
        def login_url_callback(login_url: str) -> str:
            flow.events.put(Step(NEEDS_RESPONSE_URL, {"flow_id": flow.id, "login_url": login_url}))
            return flow.input_queue("response_url").get()

        try:
            auth = ac.audible.Authenticator.from_login_external(  # type: ignore[union-attr]
                locale=flow.marketplace,
                login_url_callback=login_url_callback,
            )
            self._finish(flow, auth)
        except Exception as exc:  # pragma: no cover - network path
            log.warning("external_link_failed", error=str(exc))
            flow.error = str(exc)
            flow.events.put(Step(ERROR, {"message": str(exc)}))

    def _finish(self, flow: _Flow, auth: Any) -> None:
        # Audible's customer_info usually has no email — fall back to the account
        # holder's name so the UI can show who the account belongs to.
        owner = None
        try:
            info = getattr(auth, "customer_info", None) or {}
            if isinstance(info, dict):
                owner = info.get("email") or info.get("name") or info.get("given_name")
        except Exception:
            pass
        flow.result_blob = ac.serialize_auth(auth)
        flow.events.put(Step(LINKED, {"flow_id": flow.id, "account_owner_email": owner}))


# Process-wide singleton.
manager = LinkFlowManager()
