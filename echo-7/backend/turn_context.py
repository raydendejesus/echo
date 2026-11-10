"""
What sir said this turn, readable from inside a tool.

Protected commands check whether sir himself said his passcode this turn, rather
than trusting anything the model passes in - the model could guess or invent a
phrase, but it can't make sir have said it. The passcode itself never lands here:
it's cut out on arrival (passcode.scrub) and only the yes/no is kept. Set at the
start of each chat or phone turn; every tool awaited during that turn sees it.
"""
from contextvars import ContextVar

# What sir said, with the passcode already replaced by "[passcode]".
user_text: ContextVar[str] = ContextVar("echo_user_text", default="")
passcode_said: ContextVar[bool] = ContextVar("echo_passcode_said", default=False)
# "dashboard" (typed or spoken at the PC) or "phone" (a sir call).
channel: ContextVar[str] = ContextVar("echo_channel", default="dashboard")
