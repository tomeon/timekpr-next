"""
Created on Aug 28, 2018

@author: mjasnik
"""

import os
import webbrowser

import gi

gi.require_version("Gtk", "3.0")
import re
from datetime import datetime, timedelta

from gi.repository import Gdk, GLib, Gtk

from timekpr.client.interface.dbus.administration import timekprAdminConnector

# timekpr imports
from timekpr.common.constants import constants as cons
from timekpr.common.constants import messages as msg
from timekpr.common.log import log

# constant
_NO_TIME_LABEL = "--:--:--"
_NO_TIME_LIMIT_LABEL = "--:--:--:--"
_HOUR_REGEXP = re.compile("^([0-9]{1,2}).*$")
_HOUR_MIN_REGEXP = re.compile("^([0-9]{1,2}):([0-9]{1,2}).*$")
_DAY_HOUR_MIN_REGEXP = re.compile("^([0-9]{1,2}):([0-9]{1,2}):([0-9]{1,2}).*$")
_DAY_HOUR_MIN_SEC_REGEXP = re.compile(
    "^([0-9]{1,2}):([0-9]{1,2}):([0-9]{1,2}):([0-9]{1,2}).*$"
)


class timekprAdminGUI:
    """Main class for supporting timekpr forms"""

    def __init__(self, pTimekprVersion, pResourcePath, pUsername):
        """Initialize gui"""
        # set up base variables
        self._userName = pUsername
        self._timekprVersion = pTimekprVersion
        self._resourcePath = pResourcePath
        self._timekprAdminConnector = None
        self._isConnected = False
        # groups with a policy, as retrieved with the user list: {group: {"overrides": [...], "members": [...]}}
        self._timekprGroupInfo = {}
        self._ROWCOL_OK = "#FFFFFF"
        self._ROWSTYLE_OK = False
        self._ROWCOL_NOK = "Yellow"
        self._ROWSTYLE_NOK = True

        # ## forms builders ##
        # init config builder
        self._timekprAdminFormBuilder = Gtk.Builder()
        # get our dialog
        self._timekprAdminFormBuilder.add_from_file(
            os.path.join(self._resourcePath, "admin.glade")
        )
        # connect signals, so they get executed
        self._timekprAdminFormBuilder.connect_signals(self)
        # get window
        self._timekprAdminForm = self._timekprAdminFormBuilder.get_object(
            "TimekprApplicationWindow"
        )

        # set up GUI elements
        self.initGUIElements()

        # initialize internal stuff
        self.initInternalConfiguration()
        # disable all user config buttons firstly
        self.toggleUserConfigControls(False)
        # disable all timekpr config buttons firstly
        self.toggleTimekprConfigControls(False)
        # status
        self.setTimekprStatus(True, msg.getTranslation("TK_MSG_STATUS_STARTED"))

        # initialize internal stuff
        GLib.timeout_add_seconds(0.1, self.initTimekprAdmin)
        # periodic log flusher
        GLib.timeout_add_seconds(cons.TK_POLLTIME, self.autoFlushLogFile)

        # loop
        self._mainLoop = GLib.MainLoop()

    # --------------- initialization / helper methods --------------- #

    def startAdminGUI(self):
        """Start up main loop"""
        # show up all
        self._timekprAdminForm.show()
        # this seems to be needed
        self.dummyPageChanger()
        # start main loop
        self._mainLoop.run()

    def finishTimekpr(self, signal=None, frame=None):
        """Exit timekpr gracefully"""
        log.log(cons.TK_LOG_LEVEL_INFO, "Finishing up")
        # exit main loop
        self._mainLoop.quit()
        log.log(cons.TK_LOG_LEVEL_INFO, "Finished")
        log.flushLogFile()

    def autoFlushLogFile(self):
        """Periodically save file"""
        log.autoFlushLogFile()
        return True

    def dummyPageChanger(self):
        """Switch tabs back and forth"""
        # change pages (so objects get initialized, w/o this, spin butons don't get values when set :O)
        for rIdx in (1, 0):
            self._timekprAdminFormBuilder.get_object(
                "TimekprMainTabBar"
            ).set_current_page(rIdx)
        for rIdx in (1, 2, 0):
            self._timekprAdminFormBuilder.get_object(
                "TimekprConfigurationTabBar"
            ).set_current_page(rIdx)

    # init timekpr admin client
    def initTimekprAdmin(self):
        """Initialize admin client"""
        # get our connector
        self._timekprAdminConnector = timekprAdminConnector()
        # connect
        GLib.timeout_add_seconds(
            0, self._timekprAdminConnector.initTimekprConnection, False
        )
        # check connection
        GLib.timeout_add_seconds(0.1, self.checkConnection)
        # user calculated info retriever
        GLib.timeout_add_seconds(
            cons.TK_SAVE_INTERVAL,
            self.retrieveUserInfoAndConfig,
            None,
            cons.TK_CL_INF_SAVED,
        )
        # user "realtime" info retriever
        GLib.timeout_add_seconds(
            cons.TK_POLLTIME, self.retrieveUserInfoAndConfig, None, cons.TK_CL_INF_RT
        )

    def checkConnection(self):
        """Check connection on the fly"""
        # connection statuses
        interfacesOk, connecting = self._timekprAdminConnector.isConnected()

        # if not connected, give up and get out
        if interfacesOk and connecting:
            # status
            self.setTimekprStatus(True, msg.getTranslation("TK_MSG_STATUS_CONNECTED"))
            # in case we are connected, do not retrieve config again
            if not self._isConnected:
                # connected
                self._isConnected = True
                # get users
                GLib.timeout_add_seconds(0, self.getAdminUserList)
                GLib.timeout_add_seconds(0.1, self.retrieveTimekprConfig)
        elif not interfacesOk and connecting:
            # status
            self.setTimekprStatus(True, msg.getTranslation("TK_MSG_STATUS_CONNECTING"))
            # invoke again
            GLib.timeout_add_seconds(1, self.checkConnection)
            # not connected
            self._isConnected = False
        else:
            # status
            self.setTimekprStatus(
                True, msg.getTranslation("TK_MSG_STATUS_CONNECTION_FAILED")
            )
            self.setTimekprStatus(
                False, msg.getTranslation("TK_MSG_STATUS_CONNECTION_ACCESS_DENIED")
            )
            # not connected
            self._isConnected = False

    def initGUIElements(self):
        """Initialize all GUI elements for stores"""
        # ## tracked session types ##
        rend = Gtk.CellRendererText()
        rend.set_property("editable", True)
        rend.set_property(
            "placeholder-text", msg.getTranslation("TK_MSG_TRACKED_SESSIONS_PHLD_LABEL")
        )
        rend.connect("edited", self.timekprTrackedSessionsEdited)
        col = Gtk.TreeViewColumn(
            msg.getTranslation("TK_MSG_TRACKED_SESSIONS_LABEL"), rend, text=0
        )
        col.set_min_width(125)
        self._timekprAdminFormBuilder.get_object(
            "TimekprTrackingSessionsTreeView"
        ).append_column(col)
        # clear
        self._timekprAdminFormBuilder.get_object("TimekprTrackingSessionsLS").clear()

        # ## excluded session types ##
        rend = Gtk.CellRendererText()
        rend.set_property("editable", True)
        rend.set_property(
            "placeholder-text",
            msg.getTranslation("TK_MSG_UNTRACKED_SESSIONS_PHLD_LABEL"),
        )
        rend.connect("edited", self.timekprExcludedSessionsEdited)
        col = Gtk.TreeViewColumn(
            msg.getTranslation("TK_MSG_UNTRACKED_SESSIONS_LABEL"), rend, text=0
        )
        col.set_min_width(125)
        self._timekprAdminFormBuilder.get_object(
            "TimekprExcludedSessionsTreeView"
        ).append_column(col)
        # clear
        self._timekprAdminFormBuilder.get_object("TimekprExcludedSessionsLS").clear()

        # ## excluded users ##
        rend = Gtk.CellRendererText()
        rend.set_property("editable", True)
        rend.set_property(
            "placeholder-text", msg.getTranslation("TK_MSG_EXCLUDED_USERS_PHLD_LABEL")
        )
        rend.connect("edited", self.timekprExcludedUsersEdited)
        col = Gtk.TreeViewColumn(
            msg.getTranslation("TK_MSG_EXCLUDED_USERS_LABEL"), rend, text=0
        )
        col.set_min_width(125)
        self._timekprAdminFormBuilder.get_object(
            "TimekprExcludedUsersTreeView"
        ).append_column(col)
        # clear
        self._timekprAdminFormBuilder.get_object("TimekprExcludedUsersLS").clear()

        # ## days ##
        # day name
        rend = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(
            msg.getTranslation("TK_MSG_DAY_LIST_DAY_LABEL"), rend, text=1
        )
        col.set_min_width(115)
        self._timekprAdminFormBuilder.get_object(
            "TimekprWeekDaysTreeView"
        ).append_column(col)
        # limit
        rend = Gtk.CellRendererText()
        rend.set_property("editable", True)
        rend.connect("edited", self.userLimitsDailyLimitsEdited)
        col = Gtk.TreeViewColumn(
            msg.getTranslation("TK_MSG_DAY_LIST_LIMIT_LABEL"), rend, text=4
        )
        col.set_min_width(60)
        self._timekprAdminFormBuilder.get_object(
            "TimekprWeekDaysTreeView"
        ).append_column(col)
        # day enabled
        rend = Gtk.CellRendererToggle()
        rend.connect("toggled", self.dayAvailabilityChanged)
        col = Gtk.TreeViewColumn(
            msg.getTranslation("TK_MSG_DAY_LIST_ENABLED_LABEL"), rend, active=2
        )
        rend.set_property("activatable", True)
        col.set_min_width(35)
        self._timekprAdminFormBuilder.get_object(
            "TimekprWeekDaysTreeView"
        ).append_column(col)
        # final col
        col = Gtk.TreeViewColumn("", Gtk.CellRendererText())
        col.set_min_width(20)
        self._timekprAdminFormBuilder.get_object(
            "TimekprWeekDaysTreeView"
        ).append_column(col)

        # ## intervals ##
        # from hour
        rend = Gtk.CellRendererText()
        rend.set_property("editable", True)
        rend.set_property(
            "placeholder-text",
            msg.getTranslation("TK_MSG_DAY_INTERVALS_FROM_PHLD_LABEL"),
        )
        rend.connect("edited", self.userLimitsHourFromEdited)
        col = Gtk.TreeViewColumn(
            msg.getTranslation("TK_MSG_DAY_INTERVALS_FROM_LABEL"),
            rend,
            text=1,
            background=6,
            underline=7,
        )
        col.set_min_width(62)
        self._timekprAdminFormBuilder.get_object(
            "TimekprHourIntervalsTreeView"
        ).append_column(col)
        # to hour
        rend = Gtk.CellRendererText()
        rend.set_property("editable", True)
        rend.set_property(
            "placeholder-text", msg.getTranslation("TK_MSG_DAY_INTERVALS_TO_PHLD_LABEL")
        )
        rend.connect("edited", self.userLimitsHourToEdited)
        col = Gtk.TreeViewColumn(
            msg.getTranslation("TK_MSG_DAY_INTERVALS_TO_LABEL"),
            rend,
            text=2,
            background=6,
            underline=7,
        )
        col.set_min_width(62)
        self._timekprAdminFormBuilder.get_object(
            "TimekprHourIntervalsTreeView"
        ).append_column(col)
        # unaccountable interval column
        rend = Gtk.CellRendererToggle()
        rend.connect("toggled", self.userLimitsHourUnaccountableToggled)
        col = Gtk.TreeViewColumn("∞", rend, active=8)
        col.set_property("alignment", 0.5)
        col.set_min_width(20)
        self._timekprAdminFormBuilder.get_object(
            "TimekprHourIntervalsTreeView"
        ).append_column(col)

        # final col
        col = Gtk.TreeViewColumn("", Gtk.CellRendererText())
        col.set_min_width(20)
        self._timekprAdminFormBuilder.get_object(
            "TimekprHourIntervalsTreeView"
        ).append_column(col)

        # clear out existing intervals
        self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS").clear()
        # lets prepare week days
        for rDay in range(1, 7 + 1):
            # fill in the intervals
            self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS").append(
                [
                    str(rDay),
                    (cons.TK_DATETIME_START + timedelta(days=rDay - 1)).strftime("%A"),
                    False,
                    0,
                    _NO_TIME_LABEL,
                ]
            )

        # ## weekly / monthly limits ##
        # type
        rend = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(
            msg.getTranslation("TK_MSG_WK_MON_LABEL"), rend, text=1
        )
        col.set_min_width(90)
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfWkMonLimitsTreeView"
        ).append_column(col)
        # weekly/monthly limit
        rend = Gtk.CellRendererText()
        rend.set_property("editable", True)
        rend.connect("edited", self.userLimitsWeeklyLimitsEdited)
        col = Gtk.TreeViewColumn(
            msg.getTranslation("TK_MSG_WK_MON_LIMIT_LABEL"), rend, text=3
        )
        col.set_min_width(95)
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfWkMonLimitsTreeView"
        ).append_column(col)
        # final col
        col = Gtk.TreeViewColumn("", Gtk.CellRendererText())
        col.set_min_width(20)
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfWkMonLimitsTreeView"
        ).append_column(col)
        # clear out existing intervals
        self._timekprAdminFormBuilder.get_object("TimekprUserConfWkMonLimitsLS").clear()
        # lets prepare week days
        for rType in (
            ("WK", msg.getTranslation("TK_MSG_WEEKLY_LABEL")),
            ("MON", msg.getTranslation("TK_MSG_MONTHLY_LABEL")),
        ):
            # fill in the intervals
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfWkMonLimitsLS"
            ).append([rType[0], rType[1], 0, _NO_TIME_LIMIT_LABEL])

    # --------------- GUI control methods --------------- #

    def initInternalConfiguration(self):
        """Initialize the internal configuration for admin form"""
        self._timekprUserConfigControlElements = [
            # combo
            "TimekprUserSelectionCB",
            # combom refresh
            "TimekprUserSelectionRefreshBT",
            # new group policy
            "TimekprNewGroupEntry",
            "TimekprNewGroupBT",
            # policy deletion
            "TimekprUserPolicyDeleteBT",
            # group overrides
            "TimekprUserConfAddOptsOverridesEntry",
            # control buttons
            "TimekprUserConfDaySettingsApplyBT",
            "TimekprUserConfTodaySettingsSetAddBT",
            "TimekprUserConfTodaySettingsSetSubractBT",
            "TimekprUserConfTodaySettingsSetSetBT",
            "TimekprUserConfDaySettingsSetDaysIntervalsVerifyBT",
            "TimekprUserConfAddOptsApplyBT",
            # check box
            "TimekprUserConfTodaySettingsTrackInactiveCB",
            "TimekprUserConfTodaySettingsHideTrayIconCB",
            # spin buttons for adjustments
            "TimekprUserConfTodaySettingsSetMinSB",
            "TimekprUserConfTodaySettingsSetHrSB",
            # lists
            "TimekprWeekDaysTreeView",
            "TimekprHourIntervalsTreeView",
            "TimekprUserConfWkMonLimitsTreeView",
            # radio / control groups
            "TimekprUserConfDaySettingsSetDaysHeaderControlBX",
            "TimekprUserConfDaySettingsSetDaysIntervalsControlBX",
            "TimekprUserConfWkMonLimitsAdjustmentsBX",
            "TimekprUserConfWkMonLimitsAdjustmentControlButtonsBX",
        ]

        self._timekprConfigControlElements = [
            # control buttons
            "TimekprConfigurationApplyBT",
            # spin buttons for adjustments
            "TimekprConfigurationLoglevelSB",
            "TimekprConfigurationWarningTimeSB",
            "TimekprConfigurationPollIntervalSB",
            "TimekprConfigurationSaveTimeSB",
            "TimekprConfigurationTerminationTimeSB",
            "TimekprConfigurationFinalNotificationSB",
            # lists
            "TimekprTrackingSessionsTreeView",
            "TimekprExcludedSessionsTreeView",
            "TimekprExcludedUsersTreeView",
            # radio / control groups
            "TimekprTrackingSessionsButtonControlBX",
            "TimekprExcludedSessionsButtonControlBX",
            "TimekprExcludedUsersButtonControlBX",
        ]

        # sets up limit variables for user configuration (internal config to compare to)
        self._tkSavedCfg = {}
        self._tkSavedCfg["timeTrackInactive"] = False
        self._tkSavedCfg["timeHideTrayIcon"] = False
        # policies: where a user's effective policy comes from, a group policy's overrides
        self._tkSavedCfg["policySource"] = ""
        self._tkSavedCfg["policyGroups"] = []
        self._tkSavedCfg["timeOverrides"] = []
        self._tkSavedCfg["timeLimitWeek"] = 0
        self._tkSavedCfg["timeLimitMonth"] = 0
        self._tkSavedCfg["timeLimitDays"] = []
        self._tkSavedCfg["timeLimitDaysLimits"] = []
        self._tkSavedCfg["timeLimitDaysHoursActual"] = {}
        self._tkSavedCfg["timeLimitDaysHoursSaved"] = {}
        # initial config
        for rDay in range(1, 7 + 1):
            self._tkSavedCfg["timeLimitDaysHoursActual"][str(rDay)] = {}
            for rHour in range(23 + 1):
                self._tkSavedCfg["timeLimitDaysHoursActual"][str(rDay)][str(rHour)] = {
                    cons.TK_CTRL_SMIN: 0,
                    cons.TK_CTRL_EMIN: cons.TK_LIMIT_PER_MINUTE,
                    cons.TK_CTRL_UACC: True,
                }
        # saved means from server, actual means modified in form
        self._tkSavedCfg["timeLimitDaysHoursSaved"] = self._tkSavedCfg[
            "timeLimitDaysHoursActual"
        ].copy()

        # sets up limit variables for timekpr configuration
        self._tkSavedCfg["timekprWarningTime"] = 0
        self._tkSavedCfg["timekprNotificationTime"] = 0
        self._tkSavedCfg["timekprPollingInterval"] = 0
        self._tkSavedCfg["timekprSaveTime"] = 0
        self._tkSavedCfg["timekprTerminationTime"] = 0
        self._tkSavedCfg["timekprLogLevel"] = 0
        self._tkSavedCfg["timekprTrackingSessions"] = []
        self._tkSavedCfg["timekprExcludedSessions"] = []
        self._tkSavedCfg["timekprExcludedUsers"] = []

    def clearAdminForm(self):
        """Clear and default everything to default values"""
        # ## clear form ##
        # time limits
        for rCtrl in (
            "TimekprUserConfTodayInfoSpentTodayLB",
            "TimekprUserConfTodayInfoSpentWeekLB",
            "TimekprUserConfTodayInfoSpentMonthLB",
            "TimekprUserConfTodayInfoLeftTodayLB",
            "TimekprUserConfTodayInfoLeftContLB",
            "TimekprUserConfTodayInfoInactiveLB",
        ):
            self._timekprAdminFormBuilder.get_object(rCtrl).set_text(
                _NO_TIME_LIMIT_LABEL
            )
        # check / radio boxes
        for rCtrl in (
            "TimekprUserConfTodaySettingsTrackInactiveCB",
            "TimekprUserConfTodaySettingsHideTrayIconCB",
        ):
            self._timekprAdminFormBuilder.get_object(rCtrl).set_active(False)
        # days / labels
        for rDay in self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS"):
            # clear list store
            rDay[2] = False
            rDay[3] = 0
            rDay[4] = _NO_TIME_LABEL
            # clear day config
            for rHour in range(23 + 1):
                self._tkSavedCfg["timeLimitDaysHoursActual"][rDay[0]][str(rHour)] = {
                    cons.TK_CTRL_SMIN: 0,
                    cons.TK_CTRL_EMIN: cons.TK_LIMIT_PER_MINUTE,
                    cons.TK_CTRL_UACC: False,
                }
        # clear up the intervals
        self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS").clear()
        # week / month limits
        for rWkMon in self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfWkMonLimitsLS"
        ):
            # clear list store
            rWkMon[2] = 0
            rWkMon[3] = _NO_TIME_LIMIT_LABEL
        # clear day config
        self._tkSavedCfg["timeLimitWeek"] = 0
        self._tkSavedCfg["timeLimitMonth"] = 0
        # policy information (source label, deletion, group overrides)
        self._tkSavedCfg["policySource"] = ""
        self._tkSavedCfg["policyGroups"] = []
        self._tkSavedCfg["timeOverrides"] = []
        self._timekprAdminFormBuilder.get_object("TimekprUserPolicySourceLB").set_text(
            msg.getTranslation("TK_MSG_ADMIN_POLICY_NONE")
        )
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserPolicyDeleteBT"
        ).set_sensitive(False)
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfAddOptsOverridesEntry"
        ).set_text("")
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfAddOptsOverridesEntry"
        ).set_sensitive(False)
        # the info & today page is disabled for groups only, the controls in it are handled individually
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfTodayBox"
        ).set_sensitive(True)
        # color
        for rCtrl in (
            "TimekprUserConfTodaySettingsSetAddBT",
            "TimekprUserConfTodaySettingsSetSubractBT",
            "TimekprUserConfTodaySettingsSetSetBT",
            "TimekprUserConfTodayLabel",
            "TimekprUserConfDaySettingsApplyBT",
            "TimekprUserConfDaySettingsSetDaysIntervalsVerifyBT",
            "TimekprUserConfDailyLabel",
            "TimekprUserConfAddOptsApplyBT",
            "TimekprUserConfAddOptsLabel",
        ):
            # reset
            self._timekprAdminFormBuilder.get_object(rCtrl).modify_fg(
                Gtk.StateFlags.NORMAL, None
            )

        # failure
        # disable all
        if (
            self._timekprAdminConnector is not None
            and not self._timekprAdminConnector.isConnected()[0]
        ):
            # reset
            for rCtrl in (
                "TimekprTrackingSessionsTreeView",
                "TimekprTrackingSessionsButtonControlBX",
                "TimekprExcludedSessionsTreeView",
                "TimekprExcludedSessionsButtonControlBX",
                "TimekprExcludedUsersTreeView",
                "TimekprExcludedUsersButtonControlBX",
            ):
                # reset
                self._timekprAdminFormBuilder.get_object(rCtrl).set_sensitive(False)

            # reset
            for rCtrl in (
                "TimekprConfigurationLoglevelSB",
                "TimekprConfigurationPollIntervalSB",
                "TimekprConfigurationSaveTimeSB",
                "TimekprConfigurationTerminationTimeSB",
                "TimekprConfigurationWarningTimeSB",
                "TimekprConfigurationFinalNotificationSB",
            ):
                # reset
                self._timekprAdminFormBuilder.get_object(rCtrl).set_sensitive(False)
                self._timekprAdminFormBuilder.get_object(rCtrl).set_value(0)

            # reset
            for rCtrl in (
                "TimekprTrackingSessionsLS",
                "TimekprExcludedSessionsLS",
                "TimekprExcludedUsersLS",
            ):
                # reset
                self._timekprAdminFormBuilder.get_object(rCtrl).clear()

    # --------------- DEV test methods --------------- #

    def initDEVDefaultConfig(self):
        """Initialize GUI elements for DEV mode"""
        # DEV
        # if there is data, no need to add any
        if (
            cons.TK_DEV_ACTIVE
            and 1 == 2  # noqa: PLR0133 (hand-flipped DEV switch)
            and len(self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS"))
            == 0
        ):
            # standard time intervals
            self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS").append(
                [
                    0,
                    "08:00",
                    "13:00",
                    "1",
                    0,
                    0,
                    self._ROWCOL_NOK,
                    self._ROWSTYLE_NOK,
                ]
            )
            self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS").append(
                [0, "15:00", "18:00", "1", 0, 0, self._ROWCOL_OK, self._ROWSTYLE_OK]
            )
            self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS").append(
                [0, "18:30", "22:00", "1", 0, 0, self._ROWCOL_OK, self._ROWSTYLE_OK]
            )
            self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS").append(
                [
                    0,
                    "22:30",
                    "23:00",
                    "1",
                    0,
                    0,
                    self._ROWCOL_NOK,
                    self._ROWSTYLE_NOK,
                ]
            )
            self._timekprAdminFormBuilder.get_object(
                "TimekprHourIntervalsTreeView"
            ).set_sensitive(True)

        # false
        return False

    # --------------- control  / helper methods --------------- #

    def getSelectedUserName(self):
        """Get selected username"""
        # result
        userName = None
        # is admin app connected to server
        if self._isConnected:
            # get object
            userCombobox = self._timekprAdminFormBuilder.get_object(
                "TimekprUserSelectionCB"
            )
            # get chosen index, model and actual id of the item
            userIdx = userCombobox.get_active()
            userModel = userCombobox.get_model()
            # only if we have selection
            # only if selected
            if userIdx is not None and userModel is not None and userIdx >= 0:
                # get username
                userName = userModel[userIdx][0]

        # result
        return userName

    def isGroupTarget(self, pName):
        """Whether the selected name is a group policy (@group) rather than a user"""
        return pName is not None and pName.startswith("@")

    def selectUserInList(self, pName):
        """Select the user or group row with the given id in the selector, if it is there"""
        # get object
        userCombobox = self._timekprAdminFormBuilder.get_object(
            "TimekprUserSelectionCB"
        )
        # find the row
        for rIdx, rRow in enumerate(userCombobox.get_model()):
            # this is it
            if rRow[0] == pName:
                # select
                userCombobox.set_active(rIdx)
                # found
                return True
        # not found
        return False

    def getOverridesFromEntry(self):
        """Get the group names entered in the overrides entry as a list"""
        # ";" separated names, spaces and empty items are ignored
        return [
            rGroup.strip()
            for rGroup in self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfAddOptsOverridesEntry"
            )
            .get_text()
            .split(";")
            if rGroup.strip() != ""
        ]

    def toggleUserConfigControls(self, pEnable=True, pLeaveUserList=False):
        """Enable or disable all user controls for the form"""
        # if disable
        if not pEnable:
            self.clearAdminForm()
        # apply settings to all buttons in user configuration
        for rObj in self._timekprUserConfigControlElements:
            # if we need to leave user selection intact
            if not (pLeaveUserList and rObj == "TimekprUserSelectionCB"):
                # get the button and set availability
                self._timekprAdminFormBuilder.get_object(rObj).set_sensitive(pEnable)
        # DEV / samples too
        self.initDEVDefaultConfig()

    def toggleTimekprConfigControls(self, pEnable=True):
        """Enable or disable all timekpr controls for the form"""
        # enable for timekpr main config can be done only in admin mode
        enable = pEnable and (os.geteuid() == 0 or cons.TK_DEV_ACTIVE)
        # apply settings to all buttons`in user configuration
        for rButton in self._timekprConfigControlElements:
            # get the button and set availability
            self._timekprAdminFormBuilder.get_object(rButton).set_sensitive(enable)

    def setTimekprStatus(self, pConnectionStatus, pStatus):
        """Change status of timekpr admin client"""
        if pStatus is not None:
            # connection
            if pConnectionStatus:
                # get main status
                statusBar = self._timekprAdminFormBuilder.get_object(
                    "TimekprConnectionStatusbar"
                )
            else:
                # get message status
                statusBar = self._timekprAdminFormBuilder.get_object(
                    "TimekprMessagesStatusbar"
                )

            # get context
            contextId = statusBar.get_context_id("status")
            # pop existing message and add new one
            statusBar.remove_all(contextId)
            statusBar.push(contextId, pStatus[:80])

    def normalizeAllowedDaysAndLimits(self):
        """Method will normalize allowed days and limits, in case user sets them differently"""
        # get the least of size
        limitLen = min(
            len(self._tkSavedCfg["timeLimitDays"]),
            len(self._tkSavedCfg["timeLimitDaysLimits"]),
        )
        # remove excess elements
        for rElem in ("timeLimitDays", "timeLimitDaysLimits"):
            for i in range(limitLen, len(self._tkSavedCfg[rElem])):
                self._tkSavedCfg[rElem].pop()

    # --------------- format helper methods --------------- #

    def formatTimeStr(self, pTotalSeconds, pFormatSecs=False, pFormatDays=False):
        """Format the time intervals as string label"""
        # get time out of seconds
        time = cons.TK_DATETIME_START + timedelta(seconds=pTotalSeconds)
        # day format
        isDayFmt = pTotalSeconds >= cons.TK_LIMIT_PER_DAY and not pFormatDays
        # limit
        limitDay = (
            "{}:".format(str((time - cons.TK_DATETIME_START).days).rjust(2, "0"))
            if pFormatDays
            else ""
        )
        limitHr = "{}".format(str(24 if isDayFmt else time.hour).rjust(2, "0"))
        limitMin = ":{}".format(str(0 if isDayFmt else time.minute).rjust(2, "0"))
        limitSec = (
            ":{}".format(str(0 if isDayFmt else time.second).rjust(2, "0"))
            if pFormatSecs
            else ""
        )
        limit = f"{limitDay}{limitHr}{limitMin}{limitSec}"
        # value
        return limit

    def getIntervalList(self, pDay):
        """Get intervals for use in GUI"""
        # init hours for intervals
        timeLimits = []
        startTimeStr = None
        endTimeStr = None
        startSeconds = None
        endSeconds = None
        uaccValue = None
        uaccChanged = False

        # loop through all days
        for rHour in range(23 + 1):
            # hour in str
            hourStr = str(rHour)
            # we process only hours that are available
            if hourStr in self._tkSavedCfg["timeLimitDaysHoursActual"][pDay]:
                # no value (interval was changed)
                uaccValue = (
                    self._tkSavedCfg["timeLimitDaysHoursActual"][pDay][hourStr][
                        cons.TK_CTRL_UACC
                    ]
                    if uaccValue is None
                    else uaccValue
                )
                # calc uacc changes
                uaccChanged = (
                    self._tkSavedCfg["timeLimitDaysHoursActual"][pDay][hourStr][
                        cons.TK_CTRL_UACC
                    ]
                    != uaccValue
                )

            # if interval is complete and next hour is not available or there is not a continous interval (start != 0 or unaccounted changed, so it's different)
            if (
                startTimeStr is not None
                and endTimeStr is not None
                and (
                    hourStr not in self._tkSavedCfg["timeLimitDaysHoursActual"][pDay]
                    or (
                        hourStr in self._tkSavedCfg["timeLimitDaysHoursActual"][pDay]
                        and (
                            self._tkSavedCfg["timeLimitDaysHoursActual"][pDay][hourStr][
                                cons.TK_CTRL_SMIN
                            ]
                            != 0
                            or uaccChanged is True
                        )
                    )
                )
            ):
                # add new limit interval
                timeLimits.append(
                    [startTimeStr, endTimeStr, startSeconds, endSeconds, uaccValue]
                )
                # erase values
                startTimeStr = None
                endTimeStr = None
                uaccValue = None
                uaccChanged = False

            # we process only hours that are available
            if hourStr in self._tkSavedCfg["timeLimitDaysHoursActual"][pDay]:
                # uacc value
                uaccValue = self._tkSavedCfg["timeLimitDaysHoursActual"][pDay][hourStr][
                    cons.TK_CTRL_UACC
                ]
                # if start hour is not yet defined
                if startTimeStr is None:
                    # first avaiable hour
                    startSeconds = (
                        rHour * cons.TK_LIMIT_PER_HOUR
                        + self._tkSavedCfg["timeLimitDaysHoursActual"][pDay][hourStr][
                            cons.TK_CTRL_SMIN
                        ]
                        * cons.TK_LIMIT_PER_MINUTE
                    )
                    startTimeStr = self.formatTimeStr(startSeconds)

                # define end hour
                endDate = cons.TK_DATETIME_START + timedelta(
                    hours=rHour,
                    minutes=self._tkSavedCfg["timeLimitDaysHoursActual"][str(pDay)][
                        hourStr
                    ][cons.TK_CTRL_EMIN],
                )
                endSeconds = int((endDate - cons.TK_DATETIME_START).total_seconds())
                endTimeStr = self.formatTimeStr(endSeconds)

                # if current interval changes (process end of interval) or this is it, no more hours
                if (
                    self._tkSavedCfg["timeLimitDaysHoursActual"][pDay][hourStr][
                        cons.TK_CTRL_EMIN
                    ]
                    != cons.TK_LIMIT_PER_MINUTE
                    or rHour == 23
                ):
                    # add new limit interval
                    timeLimits.append(
                        [startTimeStr, endTimeStr, startSeconds, endSeconds, uaccValue]
                    )
                    # erase values
                    startTimeStr = None
                    endTimeStr = None
                    uaccValue = None
                    uaccChanged = False

        # return intervals
        return timeLimits

    # --------------- field value helper methods --------------- #

    def getSelectedDays(self):
        """Get selected day from day list"""
        # get selected rows
        for i in range(2):
            # get selected rows
            (tm, paths) = (
                self._timekprAdminFormBuilder.get_object("TimekprWeekDaysTreeView")
                .get_selection()
                .get_selected_rows()
            )
            # if nothing is selected, set first selected row (if there is nothing, no row is active)
            sel = paths is not None
            sel = len(paths) > 0 if sel else sel
            # nothing selected
            if not sel:
                # set
                self._timekprAdminFormBuilder.get_object(
                    "TimekprWeekDaysTreeView"
                ).set_cursor(0)
            else:
                break

        # dict of id and nr of day
        days = []

        # only if there is smth selected
        if paths is not None:
            # idx
            for path in paths:
                # get iter and values
                ti = tm.get_iter(path)
                days.append({"idx": tm.get_path(ti)[0], "nr": str(tm.get_value(ti, 0))})

        # return
        return days

    def getSelectedHourInterval(self):
        """Get selected hour interval from hour interval list"""
        # refresh the child
        for i in range(2):
            # get selection
            (tm, ti) = (
                self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsTreeView")
                .get_selection()
                .get_selected()
            )
            # if nothing is selected, get first selected row (if there is nothing, no row is active)
            if ti is None:
                # set
                self._timekprAdminFormBuilder.get_object(
                    "TimekprHourIntervalsTreeView"
                ).set_cursor(0)
            else:
                break

        # only if there is smth selected
        if ti is not None:
            # idx
            intervalIdx = tm.get_path(ti)[0]
            intervalDayNr = str(tm.get_value(ti, 3))
        else:
            # nothing
            intervalIdx = None
            intervalDayNr = None

        # return
        return intervalIdx, intervalDayNr

    def getSelectedConfigElement(self, pElementName):
        """Get selected config element"""
        # refresh the child
        (tm, ti) = (
            self._timekprAdminFormBuilder.get_object(pElementName)
            .get_selection()
            .get_selected()
        )
        # return
        return tm.get_path(ti)[0] if ti is not None else None

    def sortHourIntervals(self):
        """Sort hour intervals for ease of use"""
        # sort vairables
        hours = {}

        # prepare sort
        for rIdx, rIt in enumerate(
            self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS")
        ):
            hours[rIt[4]] = rIdx

        # set sort order
        sortedHours = []
        # set up proper order
        for rKey in sorted(hours):
            # append to order
            sortedHours.append(hours[rKey])

        # reorder rows in liststore
        self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS").reorder(
            sortedHours
        )

    # --------------- additional configuration methods --------------- #

    def enableTimeControlToday(self, pEnable=True):
        """Enable buttons to add time today"""
        for rCtrl in (
            "TimekprUserConfTodaySettingsSetHrSB",
            "TimekprUserConfTodaySettingsSetMinSB",
        ):
            self._timekprAdminFormBuilder.get_object(rCtrl).set_sensitive(pEnable)

    # --------------- info retrieval methods --------------- #

    def getAdminUserList(self):
        """Get user list via dbus"""
        # store
        userStore = self._timekprAdminFormBuilder.get_object("TimekprUserSelectionLS")
        # clear up
        userStore.clear()
        userStore.append(["", ""])
        # def len
        widthInChars = 15

        # get list
        result, message, userList = self._timekprAdminConnector.getUserList()

        # all ok
        if result == 0:
            # loop and print
            for rUser in userList:
                # name
                userName = (
                    f"{rUser[0]} ({rUser[1]})"
                    if (rUser[1] is not None and rUser[1] != "")
                    else rUser[0]
                )
                # determine maxlen
                widthInChars = max(widthInChars, len(userName) - 3)
                # add user
                userStore.append([rUser[0], userName])
            # groups with a policy go after the users, as @group
            self._timekprGroupInfo = {}
            result, message, groupList = self._timekprAdminConnector.getGroupList()
            # all ok
            if result == 0:
                # loop and print
                for rGroup in groupList:
                    # name
                    groupName = f"@{rGroup[0]}"
                    # remember overrides and members (for the policy label)
                    self._timekprGroupInfo[str(rGroup[0])] = {
                        "overrides": [
                            rIt for rIt in str(rGroup[1]).split(";") if rIt != ""
                        ],
                        "members": [
                            rIt for rIt in str(rGroup[2]).split(";") if rIt != ""
                        ],
                    }
                    # determine maxlen
                    widthInChars = max(widthInChars, len(groupName) - 3)
                    # add group
                    userStore.append([groupName, groupName])
                # status
                self.setTimekprStatus(
                    False, msg.getTranslation("TK_MSG_STATUS_USERGROUPLIST_RETRIEVED")
                )
            else:
                # status (the users are still usable)
                self.setTimekprStatus(False, message)
            # enable
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserSelectionCB"
            ).set_sensitive(True)
            self.enableUserListControls()
            # adjust widht
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserSelectionCBEntry"
            ).set_width_chars(widthInChars)
            # init first selection
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserSelectionCB"
            ).set_active(0)
        else:
            # status
            self.setTimekprStatus(False, message)
            # check the connection
            self.checkConnection()

    def retrieveTimekprConfig(self):
        """Retrieve timekpr configuration"""
        # init
        timekprConfig = {}
        result = 0
        message = ""

        # get list
        result, message, timekprConfig = (
            self._timekprAdminConnector.getTimekprConfiguration()
        )

        # all ok
        if result == 0:
            # loop and print
            for rKey, rValue in timekprConfig.items():
                # check all by keys
                if rKey == "TIMEKPR_LOGLEVEL":
                    # log level
                    self._tkSavedCfg["timekprLogLevel"] = int(rValue)
                elif rKey == "TIMEKPR_POLLTIME":
                    # poll time
                    self._tkSavedCfg["timekprPollingInterval"] = int(rValue)
                elif rKey == "TIMEKPR_SAVE_TIME":
                    # save time
                    self._tkSavedCfg["timekprSaveTime"] = int(rValue)
                elif rKey == "TIMEKPR_TERMINATION_TIME":
                    # termination time
                    self._tkSavedCfg["timekprTerminationTime"] = int(rValue)
                elif rKey == "TIMEKPR_FINAL_WARNING_TIME":
                    # final warning time
                    self._tkSavedCfg["timekprWarningTime"] = int(rValue)
                elif rKey == "TIMEKPR_FINAL_NOTIFICATION_TIME":
                    # final notification time
                    self._tkSavedCfg["timekprNotificationTime"] = int(rValue)
                elif rKey == "TIMEKPR_SESSION_TYPES_CTRL":
                    # init
                    self._tkSavedCfg["timekprTrackingSessions"] = []
                    # loop through available session types
                    for rSessionType in rValue:
                        # add config
                        self._tkSavedCfg["timekprTrackingSessions"].append(
                            str(rSessionType)
                        )
                elif rKey == "TIMEKPR_SESSION_TYPES_EXCL":
                    # init
                    self._tkSavedCfg["timekprExcludedSessions"] = []
                    # loop through available session types
                    for rSessionType in rValue:
                        # add config
                        self._tkSavedCfg["timekprExcludedSessions"].append(
                            str(rSessionType)
                        )
                elif rKey == "TIMEKPR_USERS_EXCL":
                    # init
                    self._tkSavedCfg["timekprExcludedUsers"] = []
                    # loop through available users
                    for rUser in rValue:
                        # add config
                        self._tkSavedCfg["timekprExcludedUsers"].append(str(rUser))

            # apply config
            self.applyTimekprConfig()
            # determine control state
            self.calculateTimekprConfigControlAvailability()
            # status
            self.setTimekprStatus(
                False, msg.getTranslation("TK_MSG_STATUS_CONFIG_RETRIEVED")
            )
        else:
            # disable all but choser
            self.toggleUserConfigControls(False, True)
            # status
            self.setTimekprStatus(False, message)
            # check the connection
            self.checkConnection()

    def retrieveUserInfoAndConfig(self, pUserName, pInfoLvl):
        """Retrieve user configuration"""
        # clear before user
        if pInfoLvl == cons.TK_CL_INF_FULL:
            # reset form
            self.clearAdminForm()

        # no username passed, we try to find one
        userName = self.getSelectedUserName() if pUserName is None else pUserName

        # if nothing is passed, nothing is done
        if userName is not None and userName != "":
            # init
            userConfig = {}

            # get list
            result, message, userConfig = (
                self._timekprAdminConnector.getUserConfigurationAndInformation(
                    userName, pInfoLvl
                )
            )

            # all ok
            if result == 0:
                # reset if full info or realtime requested
                if pInfoLvl in (cons.TK_CL_INF_FULL, cons.TK_CL_INF_RT):
                    # reset optional information labels
                    for rCtrl in (
                        "TimekprUserConfTodayInfoLeftContLB",
                        "TimekprUserConfTodayInfoInactiveLB",
                    ):
                        self._timekprAdminFormBuilder.get_object(rCtrl).set_text(
                            _NO_TIME_LIMIT_LABEL
                        )

                # the policy keys are not there for every target (groups have no POLICY_*, users no OVERRIDES)
                if pInfoLvl == cons.TK_CL_INF_FULL:
                    self._tkSavedCfg["policySource"] = ""
                    self._tkSavedCfg["policyGroups"] = []
                    self._tkSavedCfg["timeOverrides"] = []
                    self._tkSavedCfg["timeHideTrayIcon"] = False

                # loop and print
                for rKey, rValue in userConfig.items():
                    # these ar saved values, refresh of saved or full is asked
                    if pInfoLvl in (cons.TK_CL_INF_FULL, cons.TK_CL_INF_SAVED):
                        # this info is refreshed regularly (based on config keys)
                        if rKey == "TIME_SPENT_DAY":
                            # spent
                            timeSpent = cons.TK_DATETIME_START + timedelta(
                                seconds=abs(rValue)
                            )
                            timeSpentStr = "{}:{}:{}:{}".format(
                                str((timeSpent - cons.TK_DATETIME_START).days).rjust(
                                    2, "0"
                                ),
                                str(timeSpent.hour).rjust(2, "0"),
                                str(timeSpent.minute).rjust(2, "0"),
                                str(timeSpent.second).rjust(2, "0"),
                            )
                            self._timekprAdminFormBuilder.get_object(
                                "TimekprUserConfTodayInfoSpentTodayLB"
                            ).set_text(timeSpentStr)
                        elif rKey == "TIME_SPENT_WEEK":
                            # spent week
                            timeSpentWeek = cons.TK_DATETIME_START + timedelta(
                                seconds=rValue
                            )
                            timeSpentWeekStr = "{}:{}:{}:{}".format(
                                str(
                                    (timeSpentWeek - cons.TK_DATETIME_START).days
                                ).rjust(2, "0"),
                                str(timeSpentWeek.hour).rjust(2, "0"),
                                str(timeSpentWeek.minute).rjust(2, "0"),
                                str(timeSpentWeek.second).rjust(2, "0"),
                            )
                            self._timekprAdminFormBuilder.get_object(
                                "TimekprUserConfTodayInfoSpentWeekLB"
                            ).set_text(timeSpentWeekStr)
                        elif rKey == "TIME_SPENT_MONTH":
                            # spent month
                            timeSpentMonth = cons.TK_DATETIME_START + timedelta(
                                seconds=rValue
                            )
                            timeSpentMonthStr = "{}:{}:{}:{}".format(
                                str(
                                    (timeSpentMonth - cons.TK_DATETIME_START).days
                                ).rjust(2, "0"),
                                str(timeSpentMonth.hour).rjust(2, "0"),
                                str(timeSpentMonth.minute).rjust(2, "0"),
                                str(timeSpentMonth.second).rjust(2, "0"),
                            )
                            self._timekprAdminFormBuilder.get_object(
                                "TimekprUserConfTodayInfoSpentMonthLB"
                            ).set_text(timeSpentMonthStr)
                        # show balance
                        elif rKey == "TIME_LEFT_DAY":
                            # balance
                            timeLeft = cons.TK_DATETIME_START + timedelta(
                                seconds=rValue
                            )
                            timeLeftStr = "{}:{}:{}:{}".format(
                                str((timeLeft - cons.TK_DATETIME_START).days).rjust(
                                    2, "0"
                                ),
                                str(timeLeft.hour).rjust(2, "0"),
                                str(timeLeft.minute).rjust(2, "0"),
                                str(timeLeft.second).rjust(2, "0"),
                            )
                            self._timekprAdminFormBuilder.get_object(
                                "TimekprUserConfTodayInfoLeftTodayLB"
                            ).set_text(timeLeftStr)
                    # refresh only if full or realtime asked
                    if pInfoLvl in (cons.TK_CL_INF_FULL, cons.TK_CL_INF_RT):
                        # show actual time left for continous use
                        if rKey == "ACTUAL_TIME_LEFT_CONTINUOUS":
                            # total left
                            timeLeft = cons.TK_DATETIME_START + timedelta(
                                seconds=rValue
                            )
                            timeLeftStr = "{}:{}:{}:{}".format(
                                str((timeLeft - cons.TK_DATETIME_START).days).rjust(
                                    2, "0"
                                ),
                                str(timeLeft.hour).rjust(2, "0"),
                                str(timeLeft.minute).rjust(2, "0"),
                                str(timeLeft.second).rjust(2, "0"),
                            )
                            self._timekprAdminFormBuilder.get_object(
                                "TimekprUserConfTodayInfoLeftContLB"
                            ).set_text(timeLeftStr)
                        # show actual time inactive
                        elif rKey == "ACTUAL_TIME_INACTIVE_SESSION":
                            # total left
                            timeLeft = cons.TK_DATETIME_START + timedelta(
                                seconds=rValue
                            )
                            timeLeftStr = "{}:{}:{}:{}".format(
                                str((timeLeft - cons.TK_DATETIME_START).days).rjust(
                                    2, "0"
                                ),
                                str(timeLeft.hour).rjust(2, "0"),
                                str(timeLeft.minute).rjust(2, "0"),
                                str(timeLeft.second).rjust(2, "0"),
                            )
                            self._timekprAdminFormBuilder.get_object(
                                "TimekprUserConfTodayInfoInactiveLB"
                            ).set_text(timeLeftStr)
                    # info is needed when full refresh requested
                    if pInfoLvl == cons.TK_CL_INF_FULL:
                        if rKey == "TRACK_INACTIVE":
                            # track inactive
                            self._tkSavedCfg["timeTrackInactive"] = bool(rValue)
                        elif rKey == "HIDE_TRAY_ICON":
                            # hide icon and notif
                            self._tkSavedCfg["timeHideTrayIcon"] = bool(rValue)
                        elif rKey == "ALLOWED_WEEKDAYS":
                            # empty the values
                            self._tkSavedCfg["timeLimitDays"] = []
                            # allowed weekdays
                            for rDay in rValue:
                                # set values
                                self._tkSavedCfg["timeLimitDays"].append(str(rDay))
                        elif rKey == "LIMITS_PER_WEEKDAYS":
                            # limits per allowed weekdays
                            self._tkSavedCfg["timeLimitDaysLimits"] = []
                            # allowed weekdays
                            for rDay in range(len(rValue)):
                                # add the value
                                self._tkSavedCfg["timeLimitDaysLimits"].append(
                                    int(rValue[rDay])
                                )
                        elif rKey == "LIMIT_PER_WEEK":
                            # value
                            self._tkSavedCfg["timeLimitWeek"] = int(rValue)
                        elif rKey == "LIMIT_PER_MONTH":
                            # value
                            self._tkSavedCfg["timeLimitMonth"] = int(rValue)
                        elif rKey == "POLICY_SOURCE":
                            # where a user's effective policy comes from
                            self._tkSavedCfg["policySource"] = str(rValue)
                        elif rKey == "POLICY_GROUPS":
                            # the groups whose policies are merged for the user
                            self._tkSavedCfg["policyGroups"] = [
                                str(rGroup) for rGroup in rValue
                            ]
                        elif rKey == "OVERRIDES":
                            # the groups a group policy takes precedence over
                            self._tkSavedCfg["timeOverrides"] = [
                                str(rGroup) for rGroup in rValue
                            ]
                        elif "ALLOWED_HOURS_" in rKey:
                            # determine the day
                            day = rKey[-1:]
                            self._tkSavedCfg["timeLimitDaysHoursActual"][day] = {}
                            # loop through available hours
                            for rHour, rHourMinutes in rValue.items():
                                # add config
                                self._tkSavedCfg["timeLimitDaysHoursActual"][day][
                                    str(rHour)
                                ] = {
                                    cons.TK_CTRL_SMIN: int(
                                        rHourMinutes[cons.TK_CTRL_SMIN]
                                    ),
                                    cons.TK_CTRL_EMIN: int(
                                        rHourMinutes[cons.TK_CTRL_EMIN]
                                    ),
                                    cons.TK_CTRL_UACC: bool(
                                        rHourMinutes[cons.TK_CTRL_UACC]
                                    ),
                                }
                            # set up saved config as well
                            self._tkSavedCfg["timeLimitDaysHoursSaved"][day] = (
                                self._tkSavedCfg["timeLimitDaysHoursActual"][day].copy()
                            )
                # clean up limits if full refresh requested
                if pInfoLvl == cons.TK_CL_INF_FULL:
                    self.normalizeAllowedDaysAndLimits()

                # config was updated only when full
                if pInfoLvl == cons.TK_CL_INF_FULL:
                    # status
                    self.setTimekprStatus(
                        False, msg.getTranslation("TK_MSG_STATUS_USER_CONFIG_RETRIEVED")
                    )
                    # apply config
                    self.applyUserConfig()
                    # determine control state
                    self.calculateUserConfigControlAvailability()
                    self.calculateUserAdditionalConfigControlAvailability()
                    # enable adding hours as well
                    self.enableTimeControlToday()
            else:
                # disable all but choser
                self.toggleUserConfigControls(False, True)
                # status
                self.setTimekprStatus(False, message)
                # check the connection
                self.checkConnection()

        # return
        return True

    # --------------- retrieved configuration apply methods --------------- #

    def applyTimekprConfig(self):
        """Apply user configuration after getting it from server"""
        # ## log level ##
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationLoglevelSB"
        ).set_value(self._tkSavedCfg["timekprLogLevel"])
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationLoglevelSB"
        ).set_sensitive(True)

        # ## poll time ##
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationPollIntervalSB"
        ).set_value(self._tkSavedCfg["timekprPollingInterval"])
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationPollIntervalSB"
        ).set_sensitive(True)

        # ## save time ##
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationSaveTimeSB"
        ).set_value(self._tkSavedCfg["timekprSaveTime"])
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationSaveTimeSB"
        ).set_sensitive(True)

        # ## termination time ##
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationTerminationTimeSB"
        ).set_value(self._tkSavedCfg["timekprTerminationTime"])
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationTerminationTimeSB"
        ).set_sensitive(True)

        # ## final warning time ##
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationWarningTimeSB"
        ).set_value(self._tkSavedCfg["timekprWarningTime"])
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationWarningTimeSB"
        ).set_sensitive(True)

        # ## final notification time ##
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationFinalNotificationSB"
        ).set_value(self._tkSavedCfg["timekprNotificationTime"])
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationFinalNotificationSB"
        ).set_sensitive(True)

        # ## tracking session types ###
        self._timekprAdminFormBuilder.get_object("TimekprTrackingSessionsLS").clear()
        for rSessionType in self._tkSavedCfg["timekprTrackingSessions"]:
            # add config
            self._timekprAdminFormBuilder.get_object(
                "TimekprTrackingSessionsLS"
            ).append([str(rSessionType)])
        # enable editing
        for rObj in (
            "TimekprTrackingSessionsTreeView",
            "TimekprTrackingSessionsButtonControlBX",
        ):
            self._timekprAdminFormBuilder.get_object(rObj).set_sensitive(True)
        # select first row
        if len(self._tkSavedCfg["timekprTrackingSessions"]) > 0:
            self._timekprAdminFormBuilder.get_object(
                "TimekprTrackingSessionsTreeView"
            ).set_cursor(0)
            self._timekprAdminFormBuilder.get_object(
                "TimekprTrackingSessionsTreeView"
            ).scroll_to_cell(0)

        # ## exclusion session types ##
        self._timekprAdminFormBuilder.get_object("TimekprExcludedSessionsLS").clear()
        for rSessionType in self._tkSavedCfg["timekprExcludedSessions"]:
            # add config
            self._timekprAdminFormBuilder.get_object(
                "TimekprExcludedSessionsLS"
            ).append([str(rSessionType)])
        # enable editing
        for rObj in (
            "TimekprExcludedSessionsTreeView",
            "TimekprExcludedSessionsButtonControlBX",
        ):
            self._timekprAdminFormBuilder.get_object(rObj).set_sensitive(True)
        # select first row
        if len(self._tkSavedCfg["timekprExcludedSessions"]) > 0:
            self._timekprAdminFormBuilder.get_object(
                "TimekprExcludedSessionsTreeView"
            ).set_cursor(0)
            self._timekprAdminFormBuilder.get_object(
                "TimekprExcludedSessionsTreeView"
            ).scroll_to_cell(0)

        # ## excluded users ##
        self._timekprAdminFormBuilder.get_object("TimekprExcludedUsersLS").clear()
        for rUser in self._tkSavedCfg["timekprExcludedUsers"]:
            # add config
            self._timekprAdminFormBuilder.get_object("TimekprExcludedUsersLS").append(
                [str(rUser)]
            )
        # enable editing
        for rObj in (
            "TimekprExcludedUsersTreeView",
            "TimekprExcludedUsersButtonControlBX",
        ):
            self._timekprAdminFormBuilder.get_object(rObj).set_sensitive(True)
        # select first row
        if len(self._tkSavedCfg["timekprExcludedUsers"]) > 0:
            self._timekprAdminFormBuilder.get_object(
                "TimekprExcludedUsersTreeView"
            ).set_cursor(0)
            self._timekprAdminFormBuilder.get_object(
                "TimekprExcludedUsersTreeView"
            ).scroll_to_cell(0)

        # enable / disable controls
        self.toggleTimekprConfigControls(True)

    def enableUserListControls(self):
        """Enable the controls next to the user selector (refresh, new group
        policy): they work whenever the list is there, whether or not an
        entry is selected, but toggleUserConfigControls disables them along
        with the rest when the selection is cleared"""
        for rCtrl in (
            "TimekprUserSelectionRefreshBT",
            "TimekprNewGroupEntry",
            "TimekprNewGroupBT",
        ):
            self._timekprAdminFormBuilder.get_object(rCtrl).set_sensitive(True)

    def applyUserConfig(self):
        """Apply user configuration after getting it from server"""
        # enable refresh and the new group policy controls
        self.enableUserListControls()

        # ## allowed days ###
        for rDay in range(1, 7 + 1):
            # enable certain days
            self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS")[rDay - 1][
                2
            ] = str(rDay) in self._tkSavedCfg["timeLimitDays"]

        # enable editing
        for rCtrl in (
            "TimekprWeekDaysTreeView",
            "TimekprHourIntervalsTreeView",
            "TimekprUserConfDaySettingsSetDaysHeaderControlBX",
        ):
            self._timekprAdminFormBuilder.get_object(rCtrl).set_sensitive(True)

        # ## limits per allowed days ###
        dayLimitIdx = -1
        # loop through all days
        for rDay in cons.TK_ALLOWED_WEEKDAYS.split(";"):
            # day index
            dayIdx = int(rDay) - 1
            # check whether this day is enabled
            if rDay in self._tkSavedCfg["timeLimitDays"]:
                # advance index
                dayLimitIdx += 1
            else:
                continue
            # calculate time
            limit = self.formatTimeStr(
                self._tkSavedCfg["timeLimitDaysLimits"][dayLimitIdx], True
            )
            # enable certain days
            self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS")[dayIdx][3] = (
                self._tkSavedCfg["timeLimitDaysLimits"][dayLimitIdx]
            )
            # set appropriate label as well
            self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS")[dayIdx][4] = (
                limit if rDay in self._tkSavedCfg["timeLimitDays"] else _NO_TIME_LABEL
            )

        # ## hour intervals ##
        # intervals themselves will be adjusted depending on selected day, we just enable them here
        for rCtrl in (
            "TimekprHourIntervalsTreeView",
            "TimekprUserConfDaySettingsSetDaysIntervalsControlBX",
        ):
            self._timekprAdminFormBuilder.get_object(rCtrl).set_sensitive(True)
        # select first row (if there are intervals)
        if dayLimitIdx > -1:
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfWkMonLimitsTreeView"
            ).set_cursor(0)
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfWkMonLimitsTreeView"
            ).scroll_to_cell(0)

        # ## limit per week / month ##
        for rWkDay in self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfWkMonLimitsLS"
        ):
            # week
            if rWkDay[0] == "WK":
                limit = self._tkSavedCfg["timeLimitWeek"]
            elif rWkDay[0] == "MON":
                limit = self._tkSavedCfg["timeLimitMonth"]
            rWkDay[2] = limit
            rWkDay[3] = self.formatTimeStr(limit, True, True)
        # enable editing
        for rCtrl in (
            "TimekprUserConfWkMonLimitsTreeView",
            "TimekprUserConfWkMonLimitsAdjustmentsBX",
            "TimekprUserConfWkMonLimitsAdjustmentControlButtonsBX",
        ):
            self._timekprAdminFormBuilder.get_object(rCtrl).set_sensitive(True)

        # current day
        currDay = datetime.now().isoweekday() - 1
        # determine curent day and point to it
        self._timekprAdminFormBuilder.get_object("TimekprWeekDaysTreeView").set_cursor(
            currDay
        )
        self._timekprAdminFormBuilder.get_object(
            "TimekprWeekDaysTreeView"
        ).scroll_to_cell(currDay)
        self._timekprAdminFormBuilder.get_object(
            "TimekprWeekDaysTreeView"
        ).get_selection().emit("changed")

        # ## additional config ##
        # set values for track inactive and disable notifications
        for rCtrl in (
            "TimekprUserConfTodaySettingsTrackInactiveCB",
            "TimekprUserConfTodaySettingsHideTrayIconCB",
        ):
            # set value
            self._timekprAdminFormBuilder.get_object(rCtrl).set_active(
                self._tkSavedCfg["timeTrackInactive"]
                if rCtrl == "TimekprUserConfTodaySettingsTrackInactiveCB"
                else self._tkSavedCfg["timeHideTrayIcon"]
            )
            # enable field & set button
            self._timekprAdminFormBuilder.get_object(rCtrl).set_sensitive(True)

        # ## policy information ##
        self.applyUserPolicyInfo()

    def applyUserPolicyInfo(self):
        """Show where the policy comes from and adjust the controls that differ between users and groups"""
        # what is selected
        userName = self.getSelectedUserName()
        isGroup = self.isGroupTarget(userName)
        policySource = self._tkSavedCfg["policySource"]

        # ## policy source label ##
        if isGroup:
            # group policy, with its known members
            members = self._timekprGroupInfo.get(userName[1:], {}).get("members", [])
            policyText = (
                msg.getTranslation("TK_MSG_ADMIN_POLICY_GROUP_MEMBERS")
                % (userName, ", ".join(members))
                if len(members) > 0
                else msg.getTranslation("TK_MSG_ADMIN_POLICY_GROUP") % (userName)
            )
        elif policySource == "user" and len(self._tkSavedCfg["policyGroups"]) > 0:
            # own settings, the rest from the groups
            policyText = msg.getTranslation("TK_MSG_ADMIN_POLICY_OWN_GROUPS") % (
                ", ".join(self._tkSavedCfg["policyGroups"])
            )
        elif policySource == "user":
            # own policy
            policyText = msg.getTranslation("TK_MSG_ADMIN_POLICY_OWN")
        elif policySource == "group":
            # policies of the groups
            policyText = msg.getTranslation("TK_MSG_ADMIN_POLICY_GROUPS") % (
                ", ".join(self._tkSavedCfg["policyGroups"])
            )
        elif policySource == "default":
            # nothing set up
            policyText = msg.getTranslation("TK_MSG_ADMIN_POLICY_DEFAULTS")
        elif policySource == "unresolved":
            # NSS could not say which groups the user is in
            policyText = msg.getTranslation("TK_MSG_ADMIN_POLICY_UNRESOLVED")
        else:
            # server did not say
            policyText = msg.getTranslation("TK_MSG_ADMIN_POLICY_NONE")
        self._timekprAdminFormBuilder.get_object("TimekprUserPolicySourceLB").set_text(
            policyText
        )

        # ## delete policy ##
        # only a policy that exists can be deleted (a group in the list has one)
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserPolicyDeleteBT"
        ).set_sensitive(isGroup or policySource == "user")

        # ## info & today page ##
        # groups have no counters and no time for today
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfTodayBox"
        ).set_sensitive(not isGroup)

        # ## hide tray icon ##
        # user only, so for a group it is unchecked and disabled (and never a change)
        if isGroup:
            self._tkSavedCfg["timeHideTrayIcon"] = False
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfTodaySettingsHideTrayIconCB"
            ).set_active(False)
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfTodaySettingsHideTrayIconCB"
            ).set_sensitive(False)

        # ## overrides ##
        # group only, so for a user it is empty and disabled (and never a change)
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfAddOptsOverridesEntry"
        ).set_text(";".join(self._tkSavedCfg["timeOverrides"]) if isGroup else "")
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfAddOptsOverridesEntry"
        ).set_sensitive(isGroup)

    # --------------- change detection and GUI action control methods --------------- #

    def calculateTimekprConfigControlAvailability(self, pApplyControls=True):
        """Calculate main control availability"""
        # this duplicates diff control as well
        changeControl = {}

        # perform?
        if not self._timekprAdminFormBuilder.get_object(
            "TimekprTrackingSessionsTreeView"
        ).get_sensitive():
            return changeControl

        # ## log level ##
        control = "TimekprConfigurationLoglevelSB"
        value = self._timekprAdminFormBuilder.get_object(control).get_value_as_int()
        changeControl[control] = {
            "st": value != self._tkSavedCfg["timekprLogLevel"],
            "val": value,
        }

        # ## poll time ##
        control = "TimekprConfigurationPollIntervalSB"
        value = self._timekprAdminFormBuilder.get_object(control).get_value_as_int()
        changeControl[control] = {
            "st": value != self._tkSavedCfg["timekprPollingInterval"],
            "val": value,
        }

        # ## save time ##
        control = "TimekprConfigurationSaveTimeSB"
        value = self._timekprAdminFormBuilder.get_object(control).get_value_as_int()
        changeControl[control] = {
            "st": value != self._tkSavedCfg["timekprSaveTime"],
            "val": value,
        }

        # ## termination time ##
        control = "TimekprConfigurationTerminationTimeSB"
        value = self._timekprAdminFormBuilder.get_object(control).get_value_as_int()
        changeControl[control] = {
            "st": value != self._tkSavedCfg["timekprTerminationTime"],
            "val": value,
        }

        # ## final warning time ##
        control = "TimekprConfigurationWarningTimeSB"
        value = self._timekprAdminFormBuilder.get_object(control).get_value_as_int()
        changeControl[control] = {
            "st": value != self._tkSavedCfg["timekprWarningTime"],
            "val": value,
        }

        # ## final notification ##
        control = "TimekprConfigurationFinalNotificationSB"
        value = self._timekprAdminFormBuilder.get_object(control).get_value_as_int()
        changeControl[control] = {
            "st": value != self._tkSavedCfg["timekprNotificationTime"],
            "val": value,
        }

        # ## tracking session types ###
        tmpArray = [
            str(rIt[0])
            for rIt in self._timekprAdminFormBuilder.get_object(
                "TimekprTrackingSessionsLS"
            )
            if rIt[0] != ""
        ]
        control = "TimekprTrackingSessionsLS"
        changeControl[control] = {
            "st": tmpArray != self._tkSavedCfg["timekprTrackingSessions"],
            "val": tmpArray.copy(),
        }

        # ## exclusion session types ##
        tmpArray = [
            str(rIt[0])
            for rIt in self._timekprAdminFormBuilder.get_object(
                "TimekprExcludedSessionsLS"
            )
            if rIt[0] != ""
        ]
        control = "TimekprExcludedSessionsLS"
        changeControl[control] = {
            "st": tmpArray != self._tkSavedCfg["timekprExcludedSessions"],
            "val": tmpArray.copy(),
        }

        # ## excluded users ##
        tmpArray = [
            str(rIt[0])
            for rIt in self._timekprAdminFormBuilder.get_object(
                "TimekprExcludedUsersLS"
            )
            if rIt[0] != ""
        ]
        control = "TimekprExcludedUsersLS"
        changeControl[control] = {
            "st": tmpArray != self._tkSavedCfg["timekprExcludedUsers"],
            "val": tmpArray.copy(),
        }

        # if at least one is changed
        enable = False
        if pApplyControls:
            for rVal in changeControl.values():
                # one thing changed
                if rVal["st"]:
                    # enable
                    enable = rVal["st"]
                    # no need to search further
                    break

            # enabled or not
            self._timekprAdminFormBuilder.get_object(
                "TimekprConfigurationApplyBT"
            ).set_sensitive(enable)

            # color the buttons for ppl to see them better
            self._timekprAdminFormBuilder.get_object(
                "TimekprConfigurationApplyBT"
            ).modify_fg(
                Gtk.StateFlags.NORMAL, Gdk.color_parse("red") if enable else None
            )

            # tab color
            self._timekprAdminFormBuilder.get_object(
                "TimekprConfigurationTabLabel"
            ).modify_fg(
                Gtk.StateFlags.NORMAL, Gdk.color_parse("red") if enable else None
            )

        # return
        return changeControl

    def calculateUserTodayControlAvailability(self):
        """Calculate user today config control availability"""
        # ## add time today ##
        enabled = (
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfTodaySettingsSetHrSB"
            ).get_value_as_int()
            != 0
            or self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfTodaySettingsSetMinSB"
            ).get_value_as_int()
            != 0
        )
        for rCtrl in (
            "TimekprUserConfTodaySettingsSetAddBT",
            "TimekprUserConfTodaySettingsSetSubractBT",
            "TimekprUserConfTodaySettingsSetSetBT",
        ):
            self._timekprAdminFormBuilder.get_object(rCtrl).set_sensitive(enabled)

        # color the buttons for ppl to see them better
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfTodaySettingsSetAddBT"
        ).modify_fg(Gtk.StateFlags.NORMAL, Gdk.color_parse("red") if enabled else None)
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfTodaySettingsSetSubractBT"
        ).modify_fg(Gtk.StateFlags.NORMAL, Gdk.color_parse("red") if enabled else None)
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfTodaySettingsSetSetBT"
        ).modify_fg(Gtk.StateFlags.NORMAL, Gdk.color_parse("red") if enabled else None)

        # tab color
        self._timekprAdminFormBuilder.get_object("TimekprUserConfTodayLabel").modify_fg(
            Gtk.StateFlags.NORMAL, Gdk.color_parse("red") if enabled else None
        )

    def calculateUserConfigControlAvailability(self, pApplyControls=True):
        """Calculate user config control availability"""
        # this duplicates diff control as well
        changeControl = {}

        # perform?
        if not self._timekprAdminFormBuilder.get_object(
            "TimekprWeekDaysTreeView"
        ).get_sensitive():
            return changeControl

        # get stores (for use later)
        limitSt = self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS")
        wkMonSt = self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfWkMonLimitsLS"
        )

        # ## time day config ##
        tmpArray = [str(rIt[0]) for rIt in limitSt if rIt[2]]
        control = "TimekprUserWeekDaysLSD"
        changeControl[control] = {
            "st": tmpArray != self._tkSavedCfg["timeLimitDays"],
            "val": tmpArray.copy(),
        }

        # ## time limits per allowed days ###
        tmpArray = [rIt[3] for rIt in limitSt if rIt[2]]
        control = "TimekprUserWeekDaysLSL"
        changeControl[control] = {
            "st": tmpArray != self._tkSavedCfg["timeLimitDaysLimits"],
            "val": tmpArray.copy(),
        }

        # ## intervals ###
        areIntervalsVerified = self.areHoursVerified()
        control = "TimekprHourIntervalsLS"
        changeControl[control] = {
            "st": self._tkSavedCfg["timeLimitDaysHoursActual"]
            != self._tkSavedCfg["timeLimitDaysHoursSaved"],
            "val": self._tkSavedCfg["timeLimitDaysHoursActual"],
        }

        # ## week / month limits ###
        for rIt in wkMonSt:
            # week or month?
            if rIt[0] == "WK":
                control = "TimekprUserConfWkMonLimitsLSWK"
                changeControl[control] = {
                    "st": rIt[2] != self._tkSavedCfg["timeLimitWeek"],
                    "val": rIt[2],
                }
            elif rIt[0] == "MON":
                control = "TimekprUserConfWkMonLimitsLSMON"
                changeControl[control] = {
                    "st": rIt[2] != self._tkSavedCfg["timeLimitMonth"],
                    "val": rIt[2],
                }

        # if at least one is changed
        configChanged = False
        if pApplyControls:
            for rVal in changeControl.values():
                # one thing changed
                if rVal["st"]:
                    # enable
                    configChanged = rVal["st"]
                    # no need to search further
                    break

            # enabled or not
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfDaySettingsApplyBT"
            ).set_sensitive(configChanged and areIntervalsVerified)

            # color the buttons for ppl to see them better
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfDaySettingsApplyBT"
            ).modify_fg(
                Gtk.StateFlags.NORMAL, Gdk.color_parse("red") if configChanged else None
            )

        # enable / disable verify
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfDaySettingsSetDaysIntervalsVerifyBT"
        ).set_sensitive(not areIntervalsVerified)

        # color the buttons for ppl to see them better
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfDaySettingsSetDaysIntervalsVerifyBT"
        ).modify_fg(
            Gtk.StateFlags.NORMAL,
            Gdk.color_parse("red") if not areIntervalsVerified else None,
        )

        # tab color
        self._timekprAdminFormBuilder.get_object("TimekprUserConfDailyLabel").modify_fg(
            Gtk.StateFlags.NORMAL,
            Gdk.color_parse("red")
            if (configChanged or not areIntervalsVerified)
            else None,
        )

        # return
        return changeControl

    def calculateUserAdditionalConfigControlAvailability(self, pApplyControls=True):
        """Calculate user config control availability"""
        # this duplicates diff control as well
        changeControl = {}

        # ## Track inactive enabled ##
        control = "TimekprUserConfTodaySettingsTrackInactiveCB"
        value = self._timekprAdminFormBuilder.get_object(control).get_active()
        changeControl[control] = {
            "st": value != self._tkSavedCfg["timeTrackInactive"],
            "val": value,
        }

        # ## Hide try icon enabled ##
        control = "TimekprUserConfTodaySettingsHideTrayIconCB"
        value = self._timekprAdminFormBuilder.get_object(control).get_active()
        changeControl[control] = {
            "st": value != self._tkSavedCfg["timeHideTrayIcon"],
            "val": value,
        }

        # ## Overrides (group policies only, the entry is disabled and empty for users) ##
        control = "TimekprUserConfAddOptsOverridesEntry"
        value = self.getOverridesFromEntry()
        changeControl[control] = {
            "st": self._timekprAdminFormBuilder.get_object(control).get_sensitive()
            and value != self._tkSavedCfg["timeOverrides"],
            "val": value,
        }

        # if at least one is changed
        enable = False
        if pApplyControls:
            for rVal in changeControl.values():
                # one thing changed
                if rVal["st"]:
                    # enable
                    enable = rVal["st"]
                    # no need to search further
                    break

            # enabled or not
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfAddOptsApplyBT"
            ).set_sensitive(enable)

            # color the buttons for ppl to see them better
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfAddOptsApplyBT"
            ).modify_fg(
                Gtk.StateFlags.NORMAL, Gdk.color_parse("red") if enable else None
            )

            # tab color
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfAddOptsLabel"
            ).modify_fg(
                Gtk.StateFlags.NORMAL, Gdk.color_parse("red") if enable else None
            )

        # return
        return changeControl

    # --------------- changed information publish methods --------------- #

    def applyTimekprConfigurationChanges(self):
        """Apply configuration changes to server"""
        # get what's changed
        changeControl = self.calculateTimekprConfigControlAvailability(False)

        # initial values
        result = 0
        message = ""

        # loop through all changes
        for rKey, rVal in changeControl.items():
            # changed
            if rVal["st"]:
                # check what element we have, depending on that call different interface
                # ## poll time ##
                if rKey == "TimekprConfigurationLoglevelSB":
                    # call server
                    result, message = self._timekprAdminConnector.setTimekprLogLevel(
                        rVal["val"]
                    )
                    # successful call
                    if result == 0:
                        # set internal state
                        self._tkSavedCfg["timekprLogLevel"] = rVal["val"]
                # ## poll time ##
                elif rKey == "TimekprConfigurationPollIntervalSB":
                    # call server
                    result, message = self._timekprAdminConnector.setTimekprPollTime(
                        rVal["val"]
                    )
                    # successful call
                    if result == 0:
                        # set internal state
                        self._tkSavedCfg["timekprPollingInterval"] = rVal["val"]
                # ## save time ##
                elif rKey == "TimekprConfigurationSaveTimeSB":
                    # call server
                    result, message = self._timekprAdminConnector.setTimekprSaveTime(
                        rVal["val"]
                    )
                    # successful call
                    if result == 0:
                        # set internal state
                        self._tkSavedCfg["timekprSaveTime"] = rVal["val"]
                # ## termination time ##
                elif rKey == "TimekprConfigurationTerminationTimeSB":
                    # call server
                    result, message = (
                        self._timekprAdminConnector.setTimekprTerminationTime(
                            rVal["val"]
                        )
                    )
                    # successful call
                    if result == 0:
                        # set internal state
                        self._tkSavedCfg["timekprTerminationTime"] = rVal["val"]
                # ## final warning time ##
                elif rKey == "TimekprConfigurationWarningTimeSB":
                    # call server
                    result, message = (
                        self._timekprAdminConnector.setTimekprFinalWarningTime(
                            rVal["val"]
                        )
                    )
                    # successful call
                    if result == 0:
                        # set internal state
                        self._tkSavedCfg["timekprWarningTime"] = rVal["val"]
                # ## final notification ##
                elif rKey == "TimekprConfigurationFinalNotificationSB":
                    # call server
                    result, message = (
                        self._timekprAdminConnector.setTimekprFinalNotificationTime(
                            rVal["val"]
                        )
                    )
                    # successful call
                    if result == 0:
                        # set internal state
                        self._tkSavedCfg["timekprNotificationTime"] = rVal["val"]
                # ## tracking session types ###
                elif rKey == "TimekprTrackingSessionsLS":
                    # call server
                    result, message = (
                        self._timekprAdminConnector.setTimekprSessionsCtrl(rVal["val"])
                    )
                    # successful call
                    if result == 0:
                        # set internal state
                        self._tkSavedCfg["timekprTrackingSessions"] = rVal["val"].copy()
                # ## exclusion session types ##
                elif rKey == "TimekprExcludedSessionsLS":
                    # call server
                    result, message = (
                        self._timekprAdminConnector.setTimekprSessionsExcl(rVal["val"])
                    )
                    # successful call
                    if result == 0:
                        # set internal state
                        self._tkSavedCfg["timekprExcludedSessions"] = rVal["val"].copy()
                # ## excluded users ##
                elif rKey == "TimekprExcludedUsersLS":
                    # call server
                    result, message = self._timekprAdminConnector.setTimekprUsersExcl(
                        rVal["val"]
                    )
                    # successful call
                    if result == 0:
                        # set internal state
                        self._tkSavedCfg["timekprExcludedUsers"] = rVal["val"].copy()
                # if all ok
                if result != 0:
                    # status
                    self.setTimekprStatus(False, message)
                    # that's it
                    break

        # fine
        if result != 0:
            # check the connection
            self.checkConnection()
        else:
            # status
            self.setTimekprStatus(
                False, msg.getTranslation("TK_MSG_STATUS_CONFIGURATION_SAVED")
            )

        # recalc the control state
        self.calculateTimekprConfigControlAvailability()

    def applyUserTodayConfigurationChanges(self, pOperation):
        """Process actual call to set time for user"""
        # get username
        userName = self.getSelectedUserName()

        # if we have username
        if userName is not None:
            # initial values
            result = 0
            message = ""

            # spin buttons
            hrSb = "TimekprUserConfTodaySettingsSetHrSB"
            minSb = "TimekprUserConfTodaySettingsSetMinSB"

            # get time to add
            timeToAdjust = (
                self._timekprAdminFormBuilder.get_object(hrSb).get_value_as_int()
                * cons.TK_LIMIT_PER_HOUR
            )
            timeToAdjust += (
                self._timekprAdminFormBuilder.get_object(minSb).get_value_as_int()
                * cons.TK_LIMIT_PER_MINUTE
            )

            # call server
            result, message = self._timekprAdminConnector.setTimeLeft(
                userName, pOperation, timeToAdjust
            )

            # successful call
            if result == 0:
                # status
                self.setTimekprStatus(
                    False, msg.getTranslation("TK_MSG_STATUS_ADJUSTTIME_PROCESSED")
                )

                # reset values to form
                for rCtrl in (hrSb, minSb):
                    # no value to add
                    self._timekprAdminFormBuilder.get_object(rCtrl).set_value(0)
            else:
                # status
                self.setTimekprStatus(False, message)
                # check the connection
                self.checkConnection()

            # recalc the control state
            self.calculateUserTodayControlAvailability()

    def applyUserLimitConfigurationChanges(self):
        """Apply configuration changes to server"""
        # get what's changed
        changeControl = self.calculateUserConfigControlAvailability(False)

        # get username
        userName = self.getSelectedUserName()
        # initial values
        result = 0
        message = ""
        changeCnt = 0

        # loop through all changes
        for rKey, rVal in changeControl.items():
            # changed
            if rVal["st"]:
                # check what element we have, depending on that call different interface
                # ## week limit ##
                if rKey == "TimekprUserConfWkMonLimitsLSWK":
                    # call server
                    result, message = self._timekprAdminConnector.setTimeLimitForWeek(
                        userName, rVal["val"]
                    )
                    # successful call
                    if result == 0:
                        # cnt
                        changeCnt += 1
                        # set internal state
                        self._tkSavedCfg["timeLimitWeek"] = rVal["val"]
                        # print success message
                        self.setTimekprStatus(
                            False,
                            msg.getTranslation(
                                "TK_MSG_STATUS_WKMONADJUSTTIME_PROCESSED"
                            ),
                        )
                # ## month limit ##
                elif rKey == "TimekprUserConfWkMonLimitsLSMON":
                    # call server
                    result, message = self._timekprAdminConnector.setTimeLimitForMonth(
                        userName, rVal["val"]
                    )
                    # successful call
                    if result == 0:
                        # cnt
                        changeCnt += 1
                        # set internal state
                        self._tkSavedCfg["timeLimitMonth"] = rVal["val"]
                        # print success message
                        self.setTimekprStatus(
                            False,
                            msg.getTranslation(
                                "TK_MSG_STATUS_WKMONADJUSTTIME_PROCESSED"
                            ),
                        )
                # ## day config ##
                elif rKey == "TimekprUserWeekDaysLSD":
                    # call server
                    result, message = self._timekprAdminConnector.setAllowedDays(
                        userName, rVal["val"]
                    )
                    # successful call
                    if result == 0:
                        # cnt
                        changeCnt += 1
                        # set internal state
                        self._tkSavedCfg["timeLimitDays"] = rVal["val"]
                        # print success message
                        self.setTimekprStatus(
                            False,
                            msg.getTranslation("TK_MSG_STATUS_ALLOWEDDAYS_PROCESSED"),
                        )
                # ## limits per allowed days ###
                elif rKey == "TimekprUserWeekDaysLSL":
                    # call server
                    result, message = self._timekprAdminConnector.setTimeLimitForDays(
                        userName, rVal["val"]
                    )
                    # successful call
                    if result == 0:
                        # cnt
                        changeCnt += 1
                        # set internal state
                        self._tkSavedCfg["timeLimitDaysLimits"] = rVal["val"]
                        # print success message
                        self.setTimekprStatus(
                            False,
                            msg.getTranslation("TK_MSG_STATUS_TIMELIMITS_PROCESSED"),
                        )
                # ## hour allowance activities ###
                elif rKey == "TimekprHourIntervalsLS":
                    # loop through changed day hours
                    for rDay in rVal["val"]:
                        # day changed
                        hrs = (
                            None
                            if self._tkSavedCfg["timeLimitDaysHoursActual"][rDay]
                            == self._tkSavedCfg["timeLimitDaysHoursSaved"][rDay]
                            else self._tkSavedCfg["timeLimitDaysHoursActual"][rDay]
                        )
                        # hours changed
                        if hrs is not None:
                            # call server
                            result, message = (
                                self._timekprAdminConnector.setAllowedHours(
                                    userName, rDay, hrs
                                )
                            )
                            # successful call
                            if result == 0:
                                # cnt
                                changeCnt += 1
                                # set internal state
                                self._tkSavedCfg["timeLimitDaysHoursSaved"][rDay] = (
                                    self._tkSavedCfg["timeLimitDaysHoursActual"][
                                        rDay
                                    ].copy()
                                )
                                # status
                                self.setTimekprStatus(
                                    False,
                                    msg.getTranslation(
                                        "TK_MSG_STATUS_ALLOWEDHOURS_PROCESSED"
                                    ),
                                )
                            else:
                                # finish
                                break
                # if all ok
                if result != 0:
                    # status
                    self.setTimekprStatus(False, message)
                    # that's it
                    break

        # fine
        if result != 0:
            # check the connection
            self.checkConnection()
        # override messages in case more then one option was processed
        elif changeCnt > 1:
            # status
            self.setTimekprStatus(
                False,
                msg.getTranslation("TK_MSG_STATUS_USER_LIMIT_CONFIGURATION_SAVED"),
            )

        # recalc the control state
        self.calculateUserConfigControlAvailability()

    def applyUserAdditionalConfigurationChanges(self):
        """Apply configuration changes to server"""
        # get what's changed
        changeControl = self.calculateUserAdditionalConfigControlAvailability(False)

        # get username
        userName = self.getSelectedUserName()
        # initial values
        result = 0
        message = ""
        changeCnt = 0

        # loop through all changes
        for rKey, rVal in changeControl.items():
            # changed
            if rVal["st"]:
                # check what element we have, depending on that call different interface
                # ## Track inactive enabled ##
                if rKey == "TimekprUserConfTodaySettingsTrackInactiveCB":
                    # call server
                    result, message = self._timekprAdminConnector.setTrackInactive(
                        userName, rVal["val"]
                    )
                    # successful call
                    if result == 0:
                        # cnt
                        changeCnt += 1
                        # set internal state
                        self._tkSavedCfg["timeTrackInactive"] = rVal["val"]
                        # print success message
                        self.setTimekprStatus(
                            False,
                            msg.getTranslation("TK_MSG_STATUS_TRACKINACTIVE_PROCESSED"),
                        )
                # ## Hide try icon enabled ##
                elif rKey == "TimekprUserConfTodaySettingsHideTrayIconCB":
                    # call server
                    result, message = self._timekprAdminConnector.setHideTrayIcon(
                        userName, rVal["val"]
                    )
                    # successful call
                    if result == 0:
                        # cnt
                        changeCnt += 1
                        # set internal state
                        self._tkSavedCfg["timeHideTrayIcon"] = rVal["val"]
                        # print success message
                        self.setTimekprStatus(
                            False,
                            msg.getTranslation("TK_MSG_STATUS_HIDETRAYICON_PROCESSED"),
                        )
                # ## Overrides (group policies) ##
                elif rKey == "TimekprUserConfAddOptsOverridesEntry":
                    # call server
                    result, message = self._timekprAdminConnector.setOverrides(
                        userName, rVal["val"]
                    )
                    # successful call
                    if result == 0:
                        # cnt
                        changeCnt += 1
                        # set internal state
                        self._tkSavedCfg["timeOverrides"] = rVal["val"]
                        # print success message
                        self.setTimekprStatus(
                            False,
                            msg.getTranslation("TK_MSG_STATUS_OVERRIDES_PROCESSED"),
                        )
                # if all ok
                if result != 0:
                    # status
                    self.setTimekprStatus(False, message)
                    # that's it
                    break

        # fine
        if result != 0:
            # check the connection
            self.checkConnection()
        # override messages in case more then one option was processed
        elif changeCnt > 1:
            # status
            self.setTimekprStatus(
                False,
                msg.getTranslation("TK_MSG_STATUS_USER_ADDOPTS_CONFIGURATION_SAVED"),
            )

        # recalc the control state
        self.calculateUserAdditionalConfigControlAvailability()

    # --------------- GTK signal methods --------------- #

    # --------------- timekpr configuration GTK signal helper methods --------------- #

    def addElementToList(self, pName):
        """Add tracked session"""
        lstSt = self._timekprAdminFormBuilder.get_object(f"{pName}LS")
        lstTw = self._timekprAdminFormBuilder.get_object(f"{pName}TreeView")
        lstLen = len(lstSt)

        # check if the last one is not empty (no need to add more empty rows)
        if lstLen > 0 and lstSt[lstLen - 1][0] != "":
            # add empty item
            lstSt.append([""])
            # scroll to end
            lstTw.set_cursor(lstLen)
            lstTw.scroll_to_cell(lstLen)
            # verify control availability
            self.calculateTimekprConfigControlAvailability()

    def removeElementFromList(self, pName):
        """Remove tracked session"""
        # defaults
        elemIdx = self.getSelectedConfigElement(f"{pName}TreeView")
        # remove selected item
        for rIdx, rIt in enumerate(
            self._timekprAdminFormBuilder.get_object(f"{pName}LS")
        ):
            # check what to remove
            if elemIdx == rIdx:
                # remove
                self._timekprAdminFormBuilder.get_object(f"{pName}LS").remove(rIt.iter)
                # this is it
                break
        # verify control availability
        self.calculateTimekprConfigControlAvailability()

    def setTimekprExcludedTrackedValue(self, pPath, pText, pWhat):
        """Set internal representation of in-place edited value"""
        # def
        lStN = None
        if pWhat == "TrackedSessions":
            # store
            lStN = "TimekprTrackingSessionsLS"
        elif pWhat == "ExcludedSessions":
            # store
            lStN = "TimekprExcludedSessionsLS"

        elif pWhat == "ExcludedUsers":
            # store
            lStN = "TimekprExcludedUsersLS"

        # handled type
        if lStN is not None:
            # get store object
            self._timekprAdminFormBuilder.get_object(lStN)[pPath][0] = pText
            # calculate control availability
            self.calculateTimekprConfigControlAvailability()

    # --------------- timekpr configuration GTK signal methods --------------- #

    def configControlSwitchesChanged(self, evt):
        """Change any control item (warning, poll, etc.)"""
        # verify control availability
        self.calculateTimekprConfigControlAvailability()

    def trackedSessionsAddClicked(self, evt):
        """Add tracked session"""
        self.addElementToList("TimekprTrackingSessions")

    def trackedSessionsRemoveClicked(self, evt):
        """Remove tracked session"""
        self.removeElementFromList("TimekprTrackingSessions")

    def excludedSessionsAddClicked(self, evt):
        """Add excluded session"""
        self.addElementToList("TimekprExcludedSessions")

    def excludedSessionsRemoveClicked(self, evt):
        """Remove excluded session"""
        self.removeElementFromList("TimekprExcludedSessions")

    def excludedUsersAddClicked(self, evt):
        """Add excluded session"""
        self.addElementToList("TimekprExcludedUsers")

    def excludedUsersRemoveClicked(self, evt):
        """Remove excluded user"""
        self.removeElementFromList("TimekprExcludedUsers")

    def timekprTrackedSessionsEdited(self, widget, path, text):
        """Tracked session values edited"""
        self.setTimekprExcludedTrackedValue(path, text, "TrackedSessions")

    def timekprExcludedSessionsEdited(self, widget, path, text):
        """Excluded session values edited"""
        self.setTimekprExcludedTrackedValue(path, text, "ExcludedSessions")

    def timekprExcludedUsersEdited(self, widget, path, text):
        """Excluded user values edited"""
        self.setTimekprExcludedTrackedValue(path, text, "ExcludedUsers")

    def applyTimekprConfigurationChangesClicked(self, evt):
        """Apply configuration changes"""
        # disable button so it cannot be triggered again
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationApplyBT"
        ).set_sensitive(False)

        # color the buttons for ppl to see them better
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationApplyBT"
        ).modify_fg(Gtk.StateFlags.NORMAL, None)

        # tab color
        self._timekprAdminFormBuilder.get_object(
            "TimekprConfigurationTabLabel"
        ).modify_fg(
            Gtk.StateFlags.NORMAL,
            Gdk.color_parse("red")
            if self._timekprAdminFormBuilder.get_object(
                "TimekprConfigurationApplyBT"
            ).get_sensitive()
            else None,
        )

        # process setting
        self.applyTimekprConfigurationChanges()

    # --------------- user selection GTK signal methods --------------- #

    def userSelectionChanged(self, evt, pInfoLvl=None):
        """User selected"""
        # get username
        userName = self.getSelectedUserName()
        # only if connected
        if userName is not None and userName != "":
            # get user config
            self.retrieveUserInfoAndConfig(
                userName, cons.TK_CL_INF_FULL if pInfoLvl is None else pInfoLvl
            )
        else:
            # disable all
            self.toggleUserConfigControls(False, True)
            # but the list is still there to refresh and to add a group to
            if self._isConnected:
                self.enableUserListControls()

    def userConfigurationRefreshClicked(self, evt):
        """User requested config restore from server"""
        self._timekprAdminFormBuilder.get_object("TimekprUserSelectionCB").emit(
            "changed"
        )

    def deletePolicyClicked(self, evt):
        """Delete the selected user's own policy or the selected group policy, after confirmation"""
        # get username
        userName = self.getSelectedUserName()
        # nothing selected
        if userName is None or userName == "":
            return

        # what is going away
        isGroup = self.isGroupTarget(userName)
        question = msg.getTranslation(
            "TK_MSG_ADMIN_DELETE_GROUP_POLICY_QUESTION"
            if isGroup
            else "TK_MSG_ADMIN_DELETE_USER_POLICY_QUESTION"
        ) % (userName)
        # ask
        tkrMsg = Gtk.MessageDialog(
            parent=self._timekprAdminForm,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            message_format=question,
        )
        response = tkrMsg.run()
        tkrMsg.destroy()
        # not confirmed
        if response != Gtk.ResponseType.YES:
            return

        # disable button so it cannot be triggered again
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserPolicyDeleteBT"
        ).set_sensitive(False)

        # call server
        result, message = self._timekprAdminConnector.deletePolicy(userName)

        # successful call
        if result == 0:
            # status
            self.setTimekprStatus(
                False, msg.getTranslation("TK_MSG_STATUS_POLICY_DELETED")
            )
            # a deleted group policy leaves the list; a user may too (one the
            # system cannot enumerate was listed for the policy alone), and
            # is otherwise shown again with the policy that applies now
            self.getAdminUserList()
            if not isGroup:
                self.selectUserInList(userName)
        else:
            # status
            self.setTimekprStatus(False, message)
            # the policy is still there
            self._timekprAdminFormBuilder.get_object(
                "TimekprUserPolicyDeleteBT"
            ).set_sensitive(True)
            # check the connection
            self.checkConnection()

    def newGroupPolicyClicked(self, evt):
        """Create a group policy with the defaults for the group named in the entry and select it"""
        # the name, with or without the @
        groupName = (
            self._timekprAdminFormBuilder.get_object("TimekprNewGroupEntry")
            .get_text()
            .strip()
            .lstrip("@")
        )
        # nothing to do
        if groupName == "":
            self.setTimekprStatus(
                False, msg.getTranslation("TK_MSG_STATUS_GROUPNAME_MISSING")
            )
            return
        # the target
        target = f"@{groupName}"

        # the policy may already exist, then it is just selected
        result, message, _ = (
            self._timekprAdminConnector.getUserConfigurationAndInformation(
                target, cons.TK_CL_INF_FULL
            )
        )
        # no policy yet: any setter creates one (holding that setting alone)
        if result != 0:
            # call server
            result, message = self._timekprAdminConnector.setTrackInactive(
                target, False
            )
            # failed
            if result != 0:
                # status
                self.setTimekprStatus(False, message)
                # check the connection
                self.checkConnection()
                return
            # status
            self.setTimekprStatus(
                False,
                msg.getTranslation("TK_MSG_STATUS_GROUPPOLICY_CREATED") % (target),
            )

        # forget the name
        self._timekprAdminFormBuilder.get_object("TimekprNewGroupEntry").set_text("")
        # refresh the list and select the group
        self.getAdminUserList()
        if not self.selectUserInList(target):
            # status
            self.setTimekprStatus(
                False,
                msg.getTranslation("TK_MSG_STATUS_GROUPPOLICY_NOT_LISTED") % (target),
            )

    # --------------- today page GTK signal methods --------------- #

    def todayAddTimeChanged(self, evt):
        """Call control calculations when time has been added"""
        # recalc control availability
        self.calculateUserTodayControlAvailability()

    def todayAddTimeClicked(self, evt):
        """Add time to user"""
        # disable button so it cannot be triggered again
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfTodaySettingsSetAddBT"
        ).set_sensitive(False)
        # process setting
        self.applyUserTodayConfigurationChanges("+")

    def todaySubtractTimeClicked(self, evt):
        """Subtract time from user"""
        # disable button so it cannot be triggered again
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfTodaySettingsSetSubractBT"
        ).set_sensitive(False)
        # process setting
        self.applyUserTodayConfigurationChanges("-")

    def todaySetTimeClicked(self, evt):
        """Set exact time for user"""
        # disable button so it cannot be triggered again
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfTodaySettingsSetSetBT"
        ).set_sensitive(False)
        # process setting
        self.applyUserTodayConfigurationChanges("=")

    # --------------- limit configuration GTK signal helper methods --------------- #

    def verifyAndSetHourInterval(self, path, text, pIsFrom):
        """Verify and set hour values"""
        # store
        intervalSt = self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS")
        # value before
        secsBefore = intervalSt[path][4 if pIsFrom else 5]
        # def
        secs = self.verifyAndCalcLimit(text, "h")
        # if we could calculate seconds (i.e. entered text is correct)
        # if values before and after does not change (or initial ""), we do nothing
        if secs is not None and (
            secsBefore != secs
            or (
                intervalSt[path][0] == -1
                and intervalSt[path][1 if pIsFrom else 2] == ""
            )
        ):
            # format secs
            text = self.formatTimeStr(secs)
            # set values
            intervalSt[path][1 if pIsFrom else 2] = text
            intervalSt[path][4 if pIsFrom else 5] = secs
            # reset id
            intervalSt[path][0] = -1
            # calculate control availability
            self.calculateUserConfigControlAvailability()

    def verifyAndSetWeeklyLimits(self, path, text):
        """Verify and set weekly values"""
        # store
        limitsSt = self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfWkMonLimitsLS"
        )
        # value before
        secsBefore = limitsSt[path][2]
        # def
        secs = self.verifyAndCalcLimit(text, "w" if limitsSt[path][0] == "WK" else "m")
        # if we could calculate seconds (i.e. entered text is correct)
        # if values before and after does not change, we do nothing
        if secs is not None and secsBefore != secs:
            # format secs
            text = self.formatTimeStr(secs, pFormatSecs=True, pFormatDays=True)
            # set values
            limitsSt[path][3] = text
            limitsSt[path][2] = secs
            # calculate control availability
            self.calculateUserConfigControlAvailability()

    def verifyAndSetDayLimits(self, path, text):
        """Verify and set daily values"""
        # store
        limitsSt = self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS")
        # value before
        secsBefore = limitsSt[path][3]
        # def
        secs = self.verifyAndCalcLimit(text, "d")
        # if we could calculate seconds (i.e. entered text is correct)
        # if values before and after does not change, we do nothing
        if secs is not None and secsBefore != secs:
            # format secs
            text = self.formatTimeStr(secs, pFormatSecs=True)
            # set values
            limitsSt[path][4] = text
            limitsSt[path][3] = secs
            # calculate control availability
            self.calculateUserConfigControlAvailability()

    def areHoursVerified(self):
        """Return whether all hours have been verified"""
        # def
        result = True
        # store
        intervalSt = self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS")
        # loop through all
        for rInt in intervalSt:
            # check whether it has been verified (id = -1 means not verified)
            if rInt[0] < 0 and not (rInt[4] == 0 and rInt[4] == rInt[5]):
                # not verified
                result = False
                break
        # result
        return result

    def verifyAndCalcLimit(self, pLimitStr, pLimitType):
        """Parse user entered limit"""

        # add limits
        def _addLimit(pSecs, pAddSecs):
            # add limits
            return pAddSecs if pSecs is None else pSecs + pAddSecs

        # def
        secs = None
        # determine interval type and calculate seconds according to it
        try:
            # days to weeks/month
            if pLimitType in ("w", "m") and _DAY_HOUR_MIN_SEC_REGEXP.match(pLimitStr):
                # calculate seconds
                secs = min(
                    _addLimit(
                        secs, int(_DAY_HOUR_MIN_SEC_REGEXP.sub(r"\4", pLimitStr))
                    ),
                    cons.TK_LIMIT_PER_MINUTE,
                )
            # days to weeks/month
            if pLimitType in ("w", "m", "d") and _DAY_HOUR_MIN_REGEXP.match(pLimitStr):
                # calculate minutes
                secs = min(
                    _addLimit(
                        secs,
                        int(_DAY_HOUR_MIN_REGEXP.sub(r"\3", pLimitStr))
                        * (cons.TK_LIMIT_PER_MINUTE if pLimitType != "d" else 1),
                    ),
                    cons.TK_LIMIT_PER_HOUR
                    if pLimitType != "d"
                    else cons.TK_LIMIT_PER_MINUTE,
                )
            # hours/minutes or days/hours
            if _HOUR_MIN_REGEXP.match(pLimitStr):
                # calculate seconds
                secs = min(
                    _addLimit(
                        secs,
                        int(_HOUR_MIN_REGEXP.sub(r"\2", pLimitStr))
                        * (
                            cons.TK_LIMIT_PER_MINUTE
                            if pLimitType in ("h", "d")
                            else cons.TK_LIMIT_PER_HOUR
                        ),
                    ),
                    cons.TK_LIMIT_PER_HOUR
                    if pLimitType in ("h", "d")
                    else cons.TK_LIMIT_PER_DAY,
                )
            # hours / days
            if _HOUR_REGEXP.match(pLimitStr):
                # calculate seconds
                secs = min(
                    _addLimit(
                        secs,
                        int(_HOUR_REGEXP.sub(r"\1", pLimitStr))
                        * (
                            cons.TK_LIMIT_PER_HOUR
                            if pLimitType in ("h", "d")
                            else cons.TK_LIMIT_PER_DAY
                        ),
                    ),
                    cons.TK_LIMIT_PER_DAY
                    if pLimitType in ("h", "d")
                    else cons.TK_LIMIT_PER_MONTH,
                )
            # no error
            if secs is not None:
                # normalize total seconds
                secs = min(
                    secs,
                    cons.TK_LIMIT_PER_MONTH
                    if pLimitType == "m"
                    else cons.TK_LIMIT_PER_WEEK
                    if pLimitType == "w"
                    else cons.TK_LIMIT_PER_DAY,
                )
        except Exception:
            # we do not care about any errors
            secs = None
        # return seconds
        return secs

    # --------------- limit configuration GTK signal methods --------------- #

    def dayLimitsIncreaseClicked(self, evt):
        """Increase time limits"""
        self.adjustTimeLimits(pType="DailyLimits", pAdd=True)

    def dayLimitsDecreaseClicked(self, evt):
        """Decrease time limits"""
        self.adjustTimeLimits(pType="DailyLimits", pAdd=False)

    def dailyLimitsDaySelectionChanged(self, evt):
        """Set up intervals on GUI day change"""
        # refresh the child
        days = self.getSelectedDays()
        # get current seconds
        dt = datetime.now().replace(microsecond=0)
        dtd = str(datetime.date(dt).isoweekday())
        dts = int(
            (
                dt - datetime.now().replace(microsecond=0, second=0, minute=0, hour=0)
            ).total_seconds()
        )
        selIdx = 0

        # only if there is smth selected
        if len(days) > 0:
            # go to last day (this cannot and should not be calculated for everything)
            dayIdx = days[len(days) - 1]["idx"]
            dayNum = days[len(days) - 1]["nr"]
            # whether day is enabled
            enabled = self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS")[
                dayIdx
            ][2]
            limit = self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS")[
                dayIdx
            ][3]

            # clear out existing intervals
            self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS").clear()
            # fill intervals only if that day exists
            if (
                dayNum in self._tkSavedCfg["timeLimitDaysHoursActual"]
                and enabled
                and limit > 0
            ):
                # idx
                found = False
                # fill the intervals
                for idx, rInterval in enumerate(self.getIntervalList(dayNum)):
                    # exists
                    found = True
                    # determine which is the current hour
                    selIdx = (
                        idx
                        if rInterval[2] <= dts <= rInterval[3] and dtd == dayNum
                        else selIdx
                    )
                    # fill in the intervals
                    self._timekprAdminFormBuilder.get_object(
                        "TimekprHourIntervalsLS"
                    ).append(
                        [
                            idx,
                            rInterval[0],
                            rInterval[1],
                            dayNum,
                            rInterval[2],
                            rInterval[3],
                            self._ROWCOL_OK,
                            self._ROWSTYLE_OK,
                            rInterval[4],
                        ]
                    )
                # found
                if found:
                    # set selection to found row
                    self._timekprAdminFormBuilder.get_object(
                        "TimekprHourIntervalsTreeView"
                    ).set_cursor(selIdx)
                    self._timekprAdminFormBuilder.get_object(
                        "TimekprHourIntervalsTreeView"
                    ).scroll_to_cell(selIdx)

    def dayAvailabilityChanged(self, widget, path):
        """Change minutes depending on day availability"""
        # get list store
        limitSt = self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS")
        # flip the checkbox
        limitSt[path][2] = not limitSt[path][2]
        # if we have a day, restore limits
        if limitSt[path][2]:
            # if we have limits set in background store, restore them
            if limitSt[path][0] in self._tkSavedCfg["timeLimitDays"]:
                limitSt[path][3] = self._tkSavedCfg["timeLimitDaysLimits"][
                    self._tkSavedCfg["timeLimitDays"].index(limitSt[path][0])
                ]
            else:
                limitSt[path][3] = 0
            # format string too
            limitSt[path][4] = self.formatTimeStr(limitSt[path][3], True)
            # restore intervals from saved state
            self._tkSavedCfg["timeLimitDaysHoursActual"][limitSt[path][0]] = (
                self._tkSavedCfg["timeLimitDaysHoursSaved"][limitSt[path][0]].copy()
            )
            # enable interval refresh
            self._timekprAdminFormBuilder.get_object(
                "TimekprWeekDaysTreeView"
            ).get_selection().emit("changed")
        else:
            # reset hours & minutes
            limitSt[path][3] = 0
            limitSt[path][4] = _NO_TIME_LABEL
            # intervals store
            intervalsSt = self._timekprAdminFormBuilder.get_object("TimekprWeekDaysLS")
            # change interval selection as well
            for rHour in range(23 + 1):
                self._tkSavedCfg["timeLimitDaysHoursActual"][intervalsSt[path][0]][
                    str(rHour)
                ] = {
                    cons.TK_CTRL_SMIN: 0,
                    cons.TK_CTRL_EMIN: cons.TK_LIMIT_PER_MINUTE,
                    cons.TK_CTRL_UACC: False,
                }
            # clear stuff and disable intervals
            self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS").clear()

        # recalc control availability
        self.calculateUserConfigControlAvailability()

    def intervalsIncreaseClicked(self, evt):
        """Increase time limits"""
        self.adjustTimeLimits(pType="Intervals", pAdd=True)

    def intervalsDecreaseClicked(self, evt):
        """Decrease time limits"""
        self.adjustTimeLimits(pType="Intervals", pAdd=False)

    def userLimitsHourFromEdited(self, widget, path, text):
        """Set internal representation of in-place edited value"""
        self.verifyAndSetHourInterval(path, text, pIsFrom=True)

    def userLimitsHourToEdited(self, widget, path, text):
        """Set internal representation of in-place edited value"""
        self.verifyAndSetHourInterval(path, text, pIsFrom=False)

    def userLimitsWeeklyLimitsEdited(self, widget, path, text):
        """Set internal representation of in-place edited value"""
        self.verifyAndSetWeeklyLimits(path, text)

    def userLimitsDailyLimitsEdited(self, widget, path, text):
        """Set internal representation of in-place edited value"""
        self.verifyAndSetDayLimits(path, text)

    def userLimitsHourUnaccountableToggled(self, widget, path):
        """Set internal representation of in-place edited value"""
        # store
        intSt = self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS")
        # flip the checkbox
        intSt[path][8] = not intSt[path][8]
        # we need to rebuild hours
        self.rebuildHoursFromIntervals()
        # calculate control availability
        self.calculateUserConfigControlAvailability()

    def addHourIntervalClicked(self, evt):
        """Add hour interval placeholder to the list"""
        limitsSt = self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS")
        limitsTw = self._timekprAdminFormBuilder.get_object(
            "TimekprHourIntervalsTreeView"
        )
        limitsLen = len(limitsSt)
        # add
        addRow = True

        # check if the last one is not empty (no need to add more empty rows)
        if (
            limitsLen > 0
            and limitsSt[limitsLen - 1][4] == 0
            and limitsSt[limitsLen - 1][5] == 0
        ):
            addRow = False
        # we can add the row
        if addRow:
            # get day to which add the interval
            days = self.getSelectedDays()
            # if it's not selected
            if len(days) < 1:
                # status
                self.setTimekprStatus(
                    False, msg.getTranslation("TK_MSG_STATUS_NODAY_SELECTED")
                )
            else:
                # normalize day
                calcDay = days[len(days) - 1]["nr"]
                # add
                limitsSt.append(
                    [
                        -1,
                        "",
                        "",
                        calcDay,
                        0,
                        0,
                        self._ROWCOL_OK,
                        self._ROWSTYLE_OK,
                        False,
                    ]
                )
                # scroll to end
                limitsTw.set_cursor(limitsLen)
                limitsTw.scroll_to_cell(limitsLen)

            # verify control availability
            self.calculateUserConfigControlAvailability()

    def removeHourIntervalClicked(self, evt):
        """Remove hour interval"""
        # defaults
        limitsSt = self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS")
        elemIdx = self.getSelectedConfigElement("TimekprHourIntervalsTreeView")
        rIdx = 0
        # only if something is selected
        if elemIdx is not None:
            # remove selected item
            for rIt in limitsSt:
                if elemIdx == rIdx:
                    # remove
                    limitsSt.remove(rIt.iter)
                    break
                # count further
                rIdx += 1

            # verify hours
            self.verifyHourIntervals(None)
            # verify control availability
            self.calculateUserConfigControlAvailability()
            # status change
            self.setTimekprStatus(
                False, msg.getTranslation("TK_MSG_STATUS_INTERVAL_REMOVED")
            )

    def wkMonLimitsIncreaseClicked(self, evt):
        """Increase week / month time limits"""
        self.adjustTimeLimits(pType="WeekMonthLimits", pAdd=True)

    def wkMonLimitsDecreaseClicked(self, evt):
        """Decrease week / month time limits"""
        self.adjustTimeLimits(pType="WeekMonthLimits", pAdd=False)

    def applyUserLimitConfigurationChangesClicked(self, evt):
        """Call set methods for changes"""
        # disable button so it cannot be triggered again
        self._timekprAdminFormBuilder.get_object(
            "TimekprUserConfDaySettingsApplyBT"
        ).set_sensitive(False)
        # process setting
        self.applyUserLimitConfigurationChanges()

    # --------------- additional page configuration GTK signal methods --------------- #

    def trackInactiveChanged(self, evt):
        """Call control calculations when inactive flag has been changed"""
        # recalc control availability
        self.calculateUserAdditionalConfigControlAvailability()

    def hideTrayIconChanged(self, evt):
        """Call control calculations when hide icon has been changed"""
        # recalc control availability
        self.calculateUserAdditionalConfigControlAvailability()

    def overridesChanged(self, evt):
        """Call control calculations when group overrides have been changed"""
        # recalc control availability
        self.calculateUserAdditionalConfigControlAvailability()

    def applyUserAdditionalConfigurationChangesClicked(self, evt):
        """Apply additional configuration changes"""
        # process setting
        self.applyUserAdditionalConfigurationChanges()

    def navigateTreeView(self, treeview, event):
        # get key name
        keyname = Gdk.keyval_name(event.keyval)
        path, col = treeview.get_cursor()
        # if there are no cols
        if path is None:
            return
        # only visible columns
        columns = [c for c in treeview.get_columns() if c.get_visible()]
        colnum = columns.index(col)
        next_column = None

        # check key
        if keyname == "Tab" or keyname == "Esc":
            # wrap
            if colnum + 1 < len(columns):
                # choose next col
                next_column = columns[colnum + 1]
            else:
                # get model
                tmodel = treeview.get_model()
                # model exists
                if tmodel is not None:
                    titer = tmodel.iter_next(tmodel.get_iter(path))
                    # there are cols
                    if titer is None:
                        titer = tmodel.get_iter_first()
                    # next
                    path = tmodel.get_path(titer)
                    next_column = columns[0]
            # key handling
            if next_column is not None:
                if keyname == "Tab":
                    GLib.timeout_add(1, treeview.set_cursor, path, next_column, True)
                elif keyname == "Escape":
                    pass

    # --------------- helper methods for signal methods --------------- #

    def adjustTimeLimits(self, pType, pAdd):
        """Recalc total seconds"""
        # get objects depending on type
        # rb format:
        #   array of: checkbutton, seconds to add, check limit, seconds in liststore, string secs in liststore, control to execute, format seconds, format days
        if pType == "DailyLimits":
            tw = "TimekprWeekDaysTreeView"
            ls = "TimekprWeekDaysLS"
            rb = [
                [
                    "TimekprUserTimeLimitsHrRB",
                    cons.TK_LIMIT_PER_HOUR,
                    cons.TK_LIMIT_PER_DAY,
                    3,
                    4,
                    self.calculateUserConfigControlAvailability,
                    True,
                    False,
                ],
                [
                    "TimekprUserTimeLimitsMinRB",
                    cons.TK_LIMIT_PER_MINUTE,
                    cons.TK_LIMIT_PER_DAY,
                    3,
                    4,
                    self.calculateUserConfigControlAvailability,
                    True,
                    False,
                ],
            ]
        elif pType == "WeekMonthLimits":
            tw = "TimekprUserConfWkMonLimitsTreeView"
            ls = "TimekprUserConfWkMonLimitsLS"
            rb = [
                [
                    "TimekprUserConfWkMonLimitsAdjustmentDayRB",
                    cons.TK_LIMIT_PER_DAY,
                    None,
                    2,
                    3,
                    self.calculateUserConfigControlAvailability,
                    True,
                    True,
                ],
                [
                    "TimekprUserConfWkMonLimitsAdjustmentHrRB",
                    cons.TK_LIMIT_PER_HOUR,
                    None,
                    2,
                    3,
                    self.calculateUserConfigControlAvailability,
                    True,
                    True,
                ],
                [
                    "TimekprUserConfWkMonLimitsAdjustmentMinRB",
                    cons.TK_LIMIT_PER_MINUTE,
                    None,
                    2,
                    3,
                    self.calculateUserConfigControlAvailability,
                    True,
                    True,
                ],
            ]
        elif pType == "Intervals":
            tw = "TimekprHourIntervalsTreeView"
            ls = "TimekprHourIntervalsLS"
            rb = [
                [
                    "TimekprUserConfDaySettingsSetDaysIntervalsHrRB",
                    cons.TK_LIMIT_PER_HOUR,
                    cons.TK_LIMIT_PER_DAY,
                    None,
                    None,
                    self.calculateUserConfigControlAvailability,
                    False,
                    False,
                ],
                [
                    "TimekprUserConfDaySettingsSetDaysIntervalsMinRB",
                    cons.TK_LIMIT_PER_MINUTE,
                    cons.TK_LIMIT_PER_DAY,
                    None,
                    None,
                    self.calculateUserConfigControlAvailability,
                    False,
                    False,
                ],
            ]
            # depending on selected items
            isFrom = self._timekprAdminFormBuilder.get_object(
                "TimekprUserConfDaySettingsSetDaysIntervalsFromRB"
            ).get_active()
            # set seconds and display idx
            for rIdx in range(len(rb)):
                # depending whether start or end is selected we change that
                rb[rIdx][3] = 4 if isFrom else 5  # seconds
                rb[rIdx][4] = 1 if isFrom else 2  # time (seconds) to display
                # opposite indexes (special case)
                rb[rIdx].append(4 if not isFrom else 5)
                rb[rIdx].append(1 if not isFrom else 2)

        # get selected rows
        (tm, paths) = (
            self._timekprAdminFormBuilder.get_object(tw)
            .get_selection()
            .get_selected_rows()
        )
        # if rows were selected
        if paths is not None:
            # def
            adj = None
            # determine adjustment amount
            for rRb in (
                rRb
                for rRb in rb
                if self._timekprAdminFormBuilder.get_object(rRb[0]).get_active()
            ):
                # if checked
                adj = rRb
            # check type found
            if adj is not None:
                # limits store
                limitsSt = self._timekprAdminFormBuilder.get_object(ls)
                # idx
                for path in paths:
                    # get idx
                    idx = tm.get_path(tm.get_iter(path))[0]
                    # for DailyLimits we do not need to adjust inactive rows
                    if pType == "DailyLimits":
                        # check if day is active
                        if not limitsSt[idx][2]:
                            # we do not process disabled days
                            continue
                    # for week / month a row that is selected makes limits different (two rows in LS)
                    elif pType == "WeekMonthLimits":
                        if limitsSt[idx][0] == "WK":
                            adj[2] = cons.TK_LIMIT_PER_WEEK
                        elif limitsSt[idx][0] == "MON":
                            adj[2] = cons.TK_LIMIT_PER_MONTH
                    # adjust value
                    secs = int(limitsSt[idx][adj[3]]) + adj[1] * (1 if pAdd else -1)
                    secs = min(adj[2], max(0, secs))
                    # set up new value
                    limitsSt[idx][adj[3]] = secs
                    limitsSt[idx][adj[4]] = self.formatTimeStr(secs, adj[6], adj[7])
                    # in case of intervals, we need to manage the end / start of it too
                    if pType == "Intervals":
                        # now check the other end whether start is later than end (from both sides)
                        if (
                            isFrom and limitsSt[idx][adj[3]] > limitsSt[idx][adj[8]]
                        ) or (
                            not isFrom and limitsSt[idx][adj[3]] < limitsSt[idx][adj[8]]
                        ):
                            # equalize
                            limitsSt[idx][adj[8]] = limitsSt[idx][adj[3]]
                        # format string
                        limitsSt[idx][adj[9]] = self.formatTimeStr(
                            limitsSt[idx][adj[8]], adj[6], adj[7]
                        )

                # verify control availability by calling configured method (check setup)
                adj[5]()
        else:
            # status
            self.setTimekprStatus(
                False, msg.getTranslation("TK_MSG_STATUS_NODAY_SELECTED")
            )

    def verifyHourIntervals(self, evt):
        """Verify hour intervals"""
        # limits store
        intervalsSt = self._timekprAdminFormBuilder.get_object("TimekprHourIntervalsLS")
        intervalIdx = -1
        result = False

        # loop through all entered intervals
        for rInt in intervalsSt:
            # get interval seconds
            intervalIdx += 1
            secondsFrom = intervalsSt[intervalIdx][4]
            secondsTo = intervalsSt[intervalIdx][5]
            # do not check empty intervals
            if secondsFrom == 0 == secondsTo and intervalsSt[intervalIdx][0] < 0:
                continue

            # len
            intervalsLen = len(intervalsSt)
            # whether interval is valid
            intervalOverlaps = False
            intervalHourConflictStart = False
            intervalHourConflictEnd = False
            intervalDuplicate = False
            intervalSameStartEnd = False
            intervalStartEndMismatch = False

            # check intervals
            for rIdx in range(intervalsLen):
                # interval boundaries
                fromSecs = intervalsSt[rIdx][4]
                toSecs = intervalsSt[rIdx][5]
                result = False
                errIdx = None
                # do not check empty intervals
                if fromSecs == 0 == toSecs and intervalsSt[rIdx][0] < 0:
                    continue

                # start is the same as end
                if secondsFrom == secondsTo:
                    # this is it
                    intervalSameStartEnd = True
                elif secondsFrom > secondsTo:
                    # this is it
                    intervalStartEndMismatch = True
                # these are for all hours
                if intervalIdx != rIdx and not (
                    intervalSameStartEnd or intervalStartEndMismatch
                ):
                    # check whether user tries to insert duplicate iterval
                    if fromSecs == secondsFrom or toSecs == secondsTo:
                        # this is it
                        intervalDuplicate = True
                    # check whether start is betwen existing interval
                    elif (
                        secondsFrom < fromSecs < secondsTo
                        or secondsFrom < toSecs < secondsTo
                    ) or (
                        fromSecs < secondsFrom < toSecs or fromSecs < secondsTo < toSecs
                    ):
                        # this is it
                        intervalOverlaps = True
                    # check whether user tries to insert iterval that doesn'y overlaps with start / end hours from other intervals, but are on the same day
                    elif int(fromSecs / cons.TK_LIMIT_PER_HOUR) <= int(
                        secondsFrom / cons.TK_LIMIT_PER_HOUR
                    ) <= int(toSecs / cons.TK_LIMIT_PER_HOUR) and int(
                        secondsFrom / cons.TK_LIMIT_PER_HOUR
                    ) * cons.TK_LIMIT_PER_HOUR not in (secondsTo, toSecs):
                        # this is it
                        intervalHourConflictStart = True
                    # check whether user tries to insert iterval that doesn'y overlaps with start / end hours from other intervals, but are on the same day
                    elif int(fromSecs / cons.TK_LIMIT_PER_HOUR) <= int(
                        secondsTo / cons.TK_LIMIT_PER_HOUR
                    ) <= int(toSecs / cons.TK_LIMIT_PER_HOUR) and int(
                        secondsTo / cons.TK_LIMIT_PER_HOUR
                    ) * cons.TK_LIMIT_PER_HOUR not in (secondsTo, toSecs):
                        # this is it
                        intervalHourConflictEnd = True

                # get final result whether intervals are ok
                result = (
                    intervalOverlaps
                    or intervalHourConflictStart
                    or intervalHourConflictEnd
                    or intervalDuplicate
                    or intervalSameStartEnd
                    or intervalStartEndMismatch
                )
                # if we have errors
                if intervalsSt[rIdx][7] != result or result:
                    # mark offending row
                    intervalsSt[rIdx][6] = (
                        self._ROWCOL_NOK if result else self._ROWCOL_OK
                    )
                    intervalsSt[rIdx][7] = (
                        self._ROWSTYLE_NOK if result else self._ROWSTYLE_OK
                    )
                    # mark checked row too
                    intervalsSt[intervalIdx][6] = (
                        self._ROWCOL_NOK if result else self._ROWCOL_OK
                    )
                    intervalsSt[intervalIdx][7] = (
                        self._ROWSTYLE_NOK if result else self._ROWSTYLE_OK
                    )
                # we have a problem
                if result:
                    # set ofending row and finish up
                    errIdx = rIdx
                    # interval is not ok, remove id
                    intervalsSt[rIdx][0] = -1
                    # exit on first error
                    break
                else:
                    # assingn an id
                    intervalsSt[intervalIdx][0] = intervalIdx

            # scroll to first error
            if result:
                # set status message if fail
                if intervalOverlaps:
                    self.setTimekprStatus(
                        False,
                        msg.getTranslation("TK_MSG_STATUS_INTERVAL_OVERLAP_DETECTED"),
                    )
                elif intervalHourConflictStart:
                    self.setTimekprStatus(
                        False,
                        msg.getTranslation(
                            "TK_MSG_STATUS_INTERVALSTART_CONFLICT_DETECTED"
                        ),
                    )
                elif intervalHourConflictEnd:
                    self.setTimekprStatus(
                        False,
                        msg.getTranslation(
                            "TK_MSG_STATUS_INTERVALEND_CONFLICT_DETECTED"
                        ),
                    )
                elif intervalDuplicate:
                    self.setTimekprStatus(
                        False,
                        msg.getTranslation("TK_MSG_STATUS_INTERVAL_DUPLICATE_DETECTED"),
                    )
                elif intervalSameStartEnd:
                    self.setTimekprStatus(
                        False,
                        msg.getTranslation(
                            "TK_MSG_STATUS_INTERVAL_STARTENDEQUAL_DETECTED"
                        ),
                    )
                elif intervalStartEndMismatch:
                    self.setTimekprStatus(
                        False,
                        msg.getTranslation(
                            "TK_MSG_STATUS_INTERVAL_ENDLESSTHANSTART_DETECTED"
                        ),
                    )
                else:
                    # sort
                    self.sortHourIntervals()
                    self.setTimekprStatus(False, "")

                # scroll
                self._timekprAdminFormBuilder.get_object(
                    "TimekprHourIntervalsTreeView"
                ).set_cursor(errIdx)
                self._timekprAdminFormBuilder.get_object(
                    "TimekprHourIntervalsTreeView"
                ).scroll_to_cell(errIdx)
                # fin
                break

        if not result:
            # sort intervals
            self.sortHourIntervals()
            # rebuild hour intervals
            self.rebuildHoursFromIntervals()

        # calculate control availability
        self.calculateUserConfigControlAvailability()

    def rebuildHoursFromIntervals(self):
        """Rebuild hours from intervals in GUI, representation to user is different than actual config"""
        # get days
        days = self.getSelectedDays()

        # day is here
        if len(days) > 0:
            # first day
            day = days[0]["nr"]
            # clear internal hour representation
            self._tkSavedCfg["timeLimitDaysHoursActual"][day] = {}

            # remove selected item
            for rIt in self._timekprAdminFormBuilder.get_object(
                "TimekprHourIntervalsLS"
            ):
                # start time
                calcTime = cons.TK_DATETIME_START + timedelta(seconds=rIt[4])
                # total seconds
                totalSeconds = rIt[5] - rIt[4]
                # failover
                maxIt = 30

                # now loop through time in interval
                while totalSeconds > 0 and maxIt > 0:
                    # failover
                    maxIt -= 1
                    # hour
                    calcHour = str(calcTime.hour)
                    # build up hour
                    self._tkSavedCfg["timeLimitDaysHoursActual"][day][calcHour] = {
                        cons.TK_CTRL_SMIN: calcTime.minute,
                        cons.TK_CTRL_EMIN: None,
                        cons.TK_CTRL_UACC: rIt[8],
                    }
                    # calc end of the hour
                    timeToSubtract = min(
                        cons.TK_LIMIT_PER_HOUR
                        - calcTime.minute * cons.TK_LIMIT_PER_MINUTE,
                        totalSeconds,
                    )
                    # adjust time
                    calcTime += timedelta(seconds=timeToSubtract)
                    # subtract hour
                    totalSeconds -= timeToSubtract

                    # add end hour
                    self._tkSavedCfg["timeLimitDaysHoursActual"][day][calcHour][
                        cons.TK_CTRL_EMIN
                    ] = (
                        cons.TK_LIMIT_PER_MINUTE
                        if calcTime.minute == 0
                        else calcTime.minute
                    )

            # set this up to all selected rows
            for rDay in days:
                # set to all days (except the one we modified)
                if rDay["nr"] != day:
                    # copy config
                    self._tkSavedCfg["timeLimitDaysHoursActual"][rDay["nr"]] = (
                        self._tkSavedCfg["timeLimitDaysHoursActual"][day].copy()
                    )

    # --------------- additional and misc methods --------------- #

    def timekprLogoClicked(self, evt, smth):
        """Open link to development support page, disabled, and maybe never will be enabled :)"""
        if 1 == 1:  # noqa: PLR0133 (hand-flipped switch, see docstring)
            pass
        elif os.geteuid() == 0:
            # copy to clipboard and show message
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(
                cons.TK_DEV_SUPPORT_PAGE, -1
            )
            tkrMsg = Gtk.MessageDialog(
                parent=self._timekprAdminForm,
                flags=Gtk.DialogFlags.MODAL,
                type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                message_format="\nDonations link copied to clipbard!\nPlease paste the address in internet browser.\nThanks for your support!",
            )
            tkrMsg.run()
            tkrMsg.destroy()
        else:
            # open link
            webbrowser.open(cons.TK_DEV_SUPPORT_PAGE, new=2, autoraise=True)

    def closePropertiesSignal(self, evt, smth):
        """Close the config form"""
        # close
        self._mainLoop.quit()
        # flush log
        log.flushLogFile()
