"""
Authorization of D-Bus callers through polkit.

The bus policy (resource/server/dbus/timekpr.conf) lets anyone send to
the server, so the server itself decides who may do what:

  * methods on the admin interfaces ask polkit whether the caller is
    authorized for an action (declared in
    resource/server/polkit/com.timekpr.server.policy); the caller may
    be asked to authenticate by an authentication agent, so the check is
    asynchronous and the reply is sent once polkit answers
  * methods on the per-user interfaces are limited to the user they are
    about (and the superuser)

The superuser is always authorized, as it would be by polkit anyway,
so administration keeps working when polkit is unavailable.
"""

# imports
import inspect
import pwd
import dbus
import dbus.service

# timekpr imports
from timekpr.common.constants import constants as cons
from timekpr.common.log import log
from timekpr.common.constants import messages as msg

# the D-Bus error clients get when they are not allowed to call a method (the same one the bus itself uses)
_ACCESS_DENIED = "org.freedesktop.DBus.Error.AccessDenied"
# CheckAuthorization flag asking polkit to involve the caller's authentication agent
_ALLOW_USER_INTERACTION = 1
# the error dbus-python raises when polkit has not answered within the timeout
_NO_REPLY = "org.freedesktop.DBus.Error.NoReply"
# keyword arguments dbus-python passes to the wrapped admin methods
_SENDER_KW = "pSender"
_REPLY_KW = "pReply"
_ERROR_KW = "pError"


def accessDenied(pMessage):
    """Build the D-Bus error for a refused call"""
    return dbus.exceptions.DBusException(pMessage, name=_ACCESS_DENIED)


class timekprPolkitAuthority(object):
    """Answers who may call what on the server"""

    def __init__(self, pBus):
        """Remember the bus, which is where callers and the polkit authority are"""
        self._bus = pBus
        # on the session bus (development only) there is no polkit subject for the caller
        self._isDevelopmentBus = isinstance(pBus, dbus.SessionBus)
        # counter making the cancellation ids of pending checks unique (polkit requires that per caller)
        self._checkCount = 0

    # --------------- caller identity --------------- #

    def getSenderUid(self, pSender):
        """Get the uid of the connection that sent the message being handled"""
        return int(self._bus.get_unix_user(pSender))

    def isSenderRoot(self, pSender):
        """Whether the caller is the superuser"""
        return self.getSenderUid(pSender) == 0

    def checkSenderIsUser(self, pUserName, pSender, pUserList):
        """Refuse the call unless it comes from the superuser or from the user it is about

        pUserList is the server's dict of tracked users, which knows their uid;
        for anyone else the uid is looked up in the password database"""
        senderUid = self.getSenderUid(pSender)
        if senderUid == 0:
            return
        if pUserName in pUserList:
            userUid = int(pUserList[pUserName].getUserId())
        else:
            try:
                userUid = pwd.getpwnam(pUserName).pw_uid
            except KeyError:
                userUid = None
        if senderUid != userUid:
            log.log(cons.TK_LOG_LEVEL_INFO, "ACCESS DENIED: uid %i (%s) called a method about user \"%s\"" % (senderUid, pSender, pUserName))
            raise accessDenied(msg.getTranslation("TK_MSG_DBUS_NOT_OWN_USER") % (pUserName))

    # --------------- polkit --------------- #

    def checkAuthorization(self, pSender, pActionId, pDetails, pAuthorized, pDenied):
        """Ask polkit whether the caller may perform the action

        Calls pAuthorized() when it may and pDenied(exception) when it may not
        or when the answer could not be obtained.  Both run in the main loop
        once polkit has answered, so the caller's authentication agent may
        take its time."""
        # the superuser is always authorized (polkit does the same, but this also works without polkit)
        if self.isSenderRoot(pSender):
            pAuthorized()
            return
        if self._isDevelopmentBus:
            log.log(cons.TK_LOG_LEVEL_INFO, "polkit: development bus, allowing %s without asking" % (pActionId))
            pAuthorized()
            return

        def _deny(pReason):
            log.log(cons.TK_LOG_LEVEL_INFO, "polkit: NOT AUTHORIZED %s for %s (%s)%s" % (pActionId, pSender, self._formatDetails(pDetails), pReason))
            pDenied(accessDenied(msg.getTranslation("TK_MSG_DBUS_NOT_AUTHORIZED") % (pActionId)))

        def _replyHandler(pResult):
            # the caller must get an answer whatever happens here
            try:
                # AuthorizationResult is one struct: (is_authorized, is_challenge, details)
                isAuthorized, isChallenge, _resultDetails = pResult
            except Exception as unexpectedException:
                _deny(", unexpected reply from polkit: %s" % (str(unexpectedException)))
                return
            if isAuthorized:
                log.log(cons.TK_LOG_LEVEL_INFO, "polkit: AUTHORIZED %s for %s (%s)" % (pActionId, pSender, self._formatDetails(pDetails)))
                pAuthorized()
            else:
                _deny(", authentication was required" if isChallenge else "")

        def _errorHandler(pException):
            # when we stop waiting, polkit should stop asking too (else the agent keeps prompting)
            if isinstance(pException, dbus.exceptions.DBusException) and pException.get_dbus_name() == _NO_REPLY:
                self._bus.call_async(
                    cons.TK_POLKIT_BUS_NAME,
                    cons.TK_POLKIT_PATH,
                    cons.TK_POLKIT_AUTHORITY_INTERFACE,
                    "CancelCheckAuthorization",
                    "s",
                    (cancellationId,),
                    None,
                    None
                )
            _deny(", error asking polkit: %s" % (str(pException)))

        # unique per pending check from this connection
        self._checkCount += 1
        cancellationId = "timekpr-%i" % (self._checkCount)
        # org.freedesktop.PolicyKit1.Authority.CheckAuthorization(Subject subject, String action_id, Dict<String,String> details, CheckAuthorizationFlags flags, String cancellation_id) -> AuthorizationResult
        subject = dbus.Struct(("system-bus-name", dbus.Dictionary({"name": pSender}, signature="sv")), signature="sa{sv}")
        self._bus.call_async(
            cons.TK_POLKIT_BUS_NAME,
            cons.TK_POLKIT_PATH,
            cons.TK_POLKIT_AUTHORITY_INTERFACE,
            "CheckAuthorization",
            "(sa{sv})sa{ss}us",
            (subject, pActionId, dbus.Dictionary(pDetails, signature="ss"), dbus.UInt32(_ALLOW_USER_INTERACTION), cancellationId),
            _replyHandler,
            _errorHandler,
            timeout=cons.TK_POLKIT_TIMEOUT
        )

    @staticmethod
    def _formatDetails(pDetails):
        """Format details for the log"""
        return ", ".join("%s=%s" % (rKey, pDetails[rKey]) for rKey in sorted(pDetails))


def timekprAuthorizedMethod(pDbusInterface, pInSignature, pOutSignature, pActionId, pUserNameArg=None):
    """Export a method on D-Bus, subject to polkit authorization

    A replacement for dbus.service.method: the method is only run once
    polkit has confirmed that the caller is authorized for pActionId, and
    its return value is sent as the reply then.  Otherwise the caller gets
    an AccessDenied error.  pUserNameArg names the argument holding the
    user the call is about; it is passed to polkit as the "user" detail
    (the method name goes as "method") so rules can scope authorizations.

    The object the method belongs to must have a timekprPolkitAuthority
    in its _timekprPolkitAuthority attribute."""
    def decorator(pMethod):
        argNames = inspect.getfullargspec(pMethod).args[1:]
        if pUserNameArg is not None and pUserNameArg not in argNames:
            raise ValueError("%s has no argument %s" % (pMethod.__name__, pUserNameArg))
        userNameIdx = argNames.index(pUserNameArg) if pUserNameArg is not None else None
        # how many values the reply carries, which decides how the return value is sent
        #   (iterating a Signature yields its complete types; len() would count characters)
        outLen = sum(1 for _rType in dbus.Signature(pOutSignature))

        def wrapper(self, *pArgs, **pKeywords):
            sender = pKeywords[_SENDER_KW]
            reply = pKeywords[_REPLY_KW]
            error = pKeywords[_ERROR_KW]

            def _authorized():
                try:
                    result = pMethod(self, *pArgs)
                except Exception as unexpectedException:
                    error(unexpectedException)
                    return
                if outLen == 0:
                    reply()
                elif outLen == 1:
                    reply(result)
                else:
                    reply(*result)

            details = {cons.TK_POLKIT_DETAIL_METHOD: pMethod.__name__}
            if userNameIdx is not None:
                details[cons.TK_POLKIT_DETAIL_USER] = str(pArgs[userNameIdx])
            self._timekprPolkitAuthority.checkAuthorization(sender, pActionId, details, _authorized, error)

        # dbus.service.method derives the D-Bus arguments from the argument
        # names, so give the wrapper the original signature plus the keywords
        wrapper.__name__ = pMethod.__name__
        wrapper.__doc__ = pMethod.__doc__
        wrapper.__signature__ = inspect.Signature(
            [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
            + [inspect.Parameter(rName, inspect.Parameter.POSITIONAL_OR_KEYWORD) for rName in argNames]
            + [inspect.Parameter(rName, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None) for rName in (_SENDER_KW, _REPLY_KW, _ERROR_KW)]
        )
        return dbus.service.method(
            pDbusInterface,
            in_signature=pInSignature,
            out_signature=pOutSignature,
            sender_keyword=_SENDER_KW,
            async_callbacks=(_REPLY_KW, _ERROR_KW)
        )(wrapper)

    return decorator
