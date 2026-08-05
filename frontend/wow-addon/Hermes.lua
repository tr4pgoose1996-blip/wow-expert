-- Hermes — WoW! Expert Game Master
-- Bootstrap. Mirrors app/main.py: creates the addon namespace, wires the
-- module registry, and exposes a slash command. All feature logic lives in
-- Core/* and Modules/* and registers itself via the registry.

local HERMES_VERSION = "0.2.0"

-- The addon-global namespace (like the Python `app` package).
Hermes = {}
Hermes.version = HERMES_VERSION
Hermes.modules = {}        -- populated by the registry
Hermes.commands = {}       -- slash subcommands

-- SavedVariables (declared in .toc as HermesDB).
HermesDB = HermesDB or {
    enabled = true,
    playerProfile = {},     -- favorite class/spec/content (mirrors PlayerProfile)
    learnedSignals = {},    -- what Hermes has learned about you
    dismissedAlerts = {},
}

local frame = CreateFrame("Frame", "HermesEventFrame", UIParent)
Hermes.frame = frame

-- Register for the events we always care about.
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")
frame:RegisterEvent("PLAYER_LOGOUT")

frame:SetScript("OnEvent", function(self, event, ...)
    if event == "ADDON_LOADED" then
        local name = ...
        if name == "Hermes" then
            Hermes:OnLoad()
        end
    elseif event == "PLAYER_ENTERING_WORLD" then
        Hermes:OnEnable()
    elseif event == "PLAYER_LOGOUT" then
        Hermes:OnDisable()
    end
end)

function Hermes:OnLoad()
    -- Modules register themselves during their file's execution (load order in
    -- .toc guarantees Core/Registry.lua ran first). Here we just finalize.
    if self.Registry and self.Registry.finalize then
        self.Registry:finalize()
    end
    self:Print(string.format("Hermes %s loaded — %d modules registered.",
        self.version, self.Registry and self.Registry:count() or 0))
end

function Hermes:OnEnable()
    if self.GM then self.GM:refreshContext() end
    self:Print("Hermes online. Type /hermes for help.")
end

function Hermes:OnDisable()
    -- Persist anything module-specific on logout.
    if self.Registry then
        self.Registry:each(function(mod) if mod.OnDisable then mod:OnDisable() end end)
    end
end

function Hermes:Print(msg)
    -- DEFAULT_CHAT_FRAME can be nil during early loading or on some clients;
    -- fall back to the first available chat frame, then to print().
    local frame = DEFAULT_CHAT_FRAME
    if not frame then frame = _G["ChatFrame1"] end
    local line = "|cff33ccffHermes:|r " .. tostring(msg)
    if frame and frame.AddMessage then
        frame:AddMessage(line)
    else
        print(line)
    end
end

-- ── Slash command ──────────────────────────────────────────────────────────
SLASH_HERMES1 = "/hermes"
SLASH_HERMES2 = "/wowexpert"
SlashCmdList["HERMES"] = function(input)
    local cmd, arg = input:match("^(%S+)%s*(.*)$")
    cmd = cmd and cmd:lower() or ""

    if cmd == "" or cmd == "help" then
        Hermes:Print("Commands:")
        Hermes:Print("  /hermes advise  — ask the Game Master what to do now")
        Hermes:Print("  /hermes coach   — open the Game Master coach panel")
        Hermes:Print("  /hermes rotation — show your rotation advisor")
        Hermes:Print("  /hermes boss    — show boss/affix alerts")
        Hermes:Print("  /hermes quests  — show quest guidance")
        Hermes:Print("  /hermes collect — collection reminders")
        Hermes:Print("  /hermes profile — set favorite class/spec/content")
        Hermes:Print("  /hermes reset   — clear learned signals")
        return
    end

    if cmd == "advise" then
        if Hermes.GM then Hermes.GM:advise(arg) end
        return
    end
    if cmd == "coach" and Hermes.CoachFrame then Hermes.CoachFrame:Show(); return end

    if cmd == "rotation" and Hermes.Rotation then Hermes.Rotation:Show() return end
    if cmd == "boss" and Hermes.BossAlerts then Hermes.BossAlerts:Show() return end
    if cmd == "quests" and Hermes.Quests then Hermes.Quests:Show() return end
    if cmd == "collect" and Hermes.Collections then Hermes.Collections:Show() return end
    if cmd == "profile" and Hermes.GM then Hermes.GM:setProfile(arg) return end
    if cmd == "reset" then
        HermesDB.learnedSignals = {}
        Hermes:Print("Learned signals cleared.")
        return
    end

    Hermes:Print("Unknown command: " .. cmd .. "  (try /hermes help)")
end
