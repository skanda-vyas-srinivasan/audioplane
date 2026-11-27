"""Public AudioPlane Python SDK.

The implementation currently retains the ``sonexis`` module as a compatibility
surface. New applications should import from ``audioplane``.
"""

from sonexis import *  # noqa: F401,F403
from sonexis import Sonexis, __all__ as _sonexis_all, __version__

from .conversation import ConversationHistory, ConversationRole, ConversationTurn
from .recovery import run_recovering_session
from .session import AudioSession, CallbackQueueFullError
from .speech import SpeechSynthesizer, TranscriptEvent, Transcriber
from .utterance import UtteranceEndReason, UtteranceEvent

AudioPlane = Sonexis
AudioPlaneClient = Sonexis

__all__ = [*_sonexis_all, "AudioPlane", "AudioPlaneClient", "AudioSession", "CallbackQueueFullError",
           "ConversationHistory", "ConversationRole", "ConversationTurn",
           "SpeechSynthesizer", "TranscriptEvent", "Transcriber",
           "UtteranceEndReason", "UtteranceEvent", "run_recovering_session"]
