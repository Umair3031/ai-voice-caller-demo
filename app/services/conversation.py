"""
Conversation service — the agent brain.

Owns one dialogue turn: append the caller's utterance to the shared session
history, render the persona's system prompt with this turn's live CRM context,
call the language model, then append and return the reply.

Session history lives in a `SessionStore` (Redis in production), so a call's
continuity no longer depends on which worker happened to receive the request.

Session isolation across a persona switch: nothing locks a `session_id` to
the persona that started it — the router accepts a `persona` field on every
turn independently, so a caller can be handed from one agent to another
mid-session. Without `_isolate_persona_switch` below, the new persona's LLM
call would receive the old persona's raw turns verbatim as plain
"assistant" messages, with no signal that a different specialist said them
— the model has no other referent for "assistant" and can pick up the prior
agent's identity, claims, or catalog. Turns from a persona other than the
current one are collapsed into one short, deterministic note instead
(never an LLM-generated summary — that would add a network round trip and
could itself hallucinate). The *stored* history is never altered by this;
only what gets sent to the model on a turn where a switch is detected.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.models import ConversationTurn, CustomerRecord, Role
from app.domain.personas.registry import PersonaId, build_system_prompt
from app.infra.state import SessionStore
from app.services.crm import render_crm_block
from app.services.protocols import LanguageModelService

logger = get_logger("services.conversation")


class ConversationService:
    def __init__(
        self,
        *,
        language_model: LanguageModelService,
        sessions: SessionStore,
        settings: Settings,
    ) -> None:
        self._llm = language_model
        self._sessions = sessions
        self._settings = settings

    async def respond(
        self,
        *,
        session_id: str,
        user_text: str,
        customer: CustomerRecord | None,
        persona: PersonaId,
    ) -> str:
        prior_history = await self._sessions.get_history(session_id)
        history_for_llm = self._isolate_persona_switch(prior_history, persona)

        user_turn = ConversationTurn(role=Role.USER, content=user_text, persona=persona.value)
        await self._sessions.append(session_id, user_turn)

        system_prompt = build_system_prompt(
            persona,
            reply_max_words=self._settings.reply_max_words,
            crm_block=render_crm_block(customer),
        )

        result = await self._llm.complete(
            system_prompt=system_prompt,
            history=self._start_with_caller([*history_for_llm, user_turn], persona),
        )
        self._check_word_budget(result.text)

        await self._sessions.append(
            session_id,
            ConversationTurn(role=Role.ASSISTANT, content=result.text, persona=persona.value),
        )
        return result.text

    async def open_call(self, *, session_id: str, persona: PersonaId, greeting: str) -> None:
        """Records the agent's opening line as the first turn of the call, so the
        caller's first reply is answered in the light of what was already said."""
        await self._sessions.append(
            session_id,
            ConversationTurn(role=Role.ASSISTANT, content=greeting, persona=persona.value),
        )

    @staticmethod
    def _start_with_caller(
        history: list[ConversationTurn], persona: PersonaId
    ) -> list[ConversationTurn]:
        """The agent speaks first, so a call's history begins with an assistant
        turn — which chat models expect to follow a user turn. A neutral,
        never-stored marker stands in for the caller picking up."""
        if history and history[0].role is Role.ASSISTANT:
            marker = ConversationTurn(
                role=Role.USER,
                content="[The caller has picked up. Your opening line follows.]",
                persona=persona.value,
            )
            return [marker, *history]
        return history

    @staticmethod
    def _isolate_persona_switch(
        history: list[ConversationTurn], persona: PersonaId
    ) -> list[ConversationTurn]:
        """Returns the history to send to the LLM, with any turns from a
        *different* persona collapsed into one note. See the module
        docstring for why. Turns stored before `persona` existed on the
        model (`turn.persona is None`) are treated as belonging to the
        current persona, not as a switch — there's no other persona to
        attribute them to.
        """
        if not history:
            return history

        current = persona.value
        own_turns = [turn for turn in history if turn.persona in (None, current)]
        other_count = len(history) - len(own_turns)
        if other_count == 0:
            return history  # no switch — unchanged, zero behavior change for the common case

        note = ConversationTurn(
            role=Role.USER,
            content=(
                f"[Call handoff note: earlier in this call, the caller exchanged "
                f"{other_count} message(s) with a different specialist. Do not refer "
                f"to that specialist's identity, claims, or catalog — continue as "
                f"yourself from here.]"
            ),
            persona=current,
        )
        return [note, *own_turns]

    async def get_history(self, session_id: str) -> list[ConversationTurn]:
        return await self._sessions.get_history(session_id)

    async def reset(self, session_id: str) -> None:
        await self._sessions.reset(session_id)

    async def active_session_count(self) -> int:
        return await self._sessions.active_session_count()

    def _check_word_budget(self, reply_text: str) -> None:
        """Soft-enforces the vocal-delivery word budget.

        The reply is deliberately NOT truncated: chopping a sentence mid-thought
        can silently drop the actual answer — a balance figure, a safety
        instruction — which is worse than an occasional over-length reply.
        Logging it instead surfaces the violation rate in monitoring so the
        prompt can be tightened where it belongs.
        """
        word_count = len(reply_text.split())
        budget = self._settings.reply_max_words
        if word_count > budget:
            logger.warning(
                "Reply exceeded the %d-word vocal-delivery budget (%d words)",
                budget,
                word_count,
                extra={"word_count": word_count, "budget": budget, "reply": reply_text[:300]},
            )
