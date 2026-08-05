-- Core/GM.lua
-- Game Master reasoning engine. Mirrors app/modules/reasoning/engine.py.
-- It does NOT answer questions — it REASONS: reads the player's situation,
-- decomposes a goal into ranked objectives pulled from each feature module,
-- and explains WHY each recommendation is optimal. This is the in-client
-- echo of the backend's Hermes brain.

Hermes.GM = {}
local GM = Hermes.GM

--- Weighted intent tokens. Mirrors the backend's _GEAR/_ROTATION/etc. groups.
--- Heavier weight = more decisive when a phrase is ambiguous.
local TOKEN_WEIGHTS = {
    gear     = { ["upgrade"] = 3, ["item level"] = 3, ["ilevel"] = 3, ["ilvl"] = 3,
                 ["trinket"] = 3, ["enchant"] = 3, ["craft"] = 3, ["gear"] = 3 },
    rotation = { ["rotation"] = 3, ["priority"] = 3, ["what to press"] = 3,
                 ["spell"] = 3, ["rotation"] = 3 },
    learn    = { ["learn"] = 2, ["how to"] = 2, ["improve"] = 2, ["practice"] = 2 },
    collect  = { ["collect"] = 2, ["mount"] = 2, ["pet"] = 2, ["toy"] = 2,
                 ["appearance"] = 2, ["achievement"] = 2, ["collection"] = 2 },
    content  = { ["raid"] = 1, ["dungeon"] = 1, ["mythic"] = 1, ["mythic+"] = 1,
                 ["m+"] = 1, ["boss"] = 1, ["clear"] = 1, ["progress"] = 1 },
    progress = { ["level"] = 2, ["catch up"] = 2, ["catch-up"] = 2,
                 ["reputation"] = 2, ["renown"] = 2 },
}

--- Source bonus: some systems are intrinsically higher-value per effort.
local SOURCE_BONUS = {
    gear = 1.2, collections = 1.1, coach = 1.0, rotation = 1.15,
    learn = 1.0, quests = 1.05, progress = 1.0,
}

--- Infer the player's intent from free text + their profile.
--- Returns a table of intent kind -> score.
function GM:inferIntent(text, profile)
    text = (text or ""):lower()
    profile = profile or {}
    local scores = { gear = 0, rotation = 0, learn = 0, collect = 0, content = 0, progress = 0 }

    for kind, tokens in pairs(TOKEN_WEIGHTS) do
        for tok, w in pairs(tokens) do
            -- Count occurrences with plain (non-pattern) matching. Lua patterns
            -- treat characters like '+', '.', '-' as metacharacters, and several
            -- of our tokens ("m+", "mythic+") contain '+', so a naive gsub would
            -- mis-count. The 4th arg `true` disables pattern matching.
            local count = 0
            local from = 1
            while true do
                local found = text:find(tok, from, true)
                if not found then break end
                count = count + 1
                from = found + #tok
            end
            scores[kind] = scores[kind] + count * w
        end
    end

    -- Profile bias: a player who loves their spec wants to improve at it.
    if profile.favoriteSpec and text:find("spec") then
        scores.learn = scores.learn + 2
    end
    if profile.favoriteContent and (text:find("what") or text == "") then
        scores.content = scores.content + 1
    end
    -- A vague "what should I do?" defaults toward content/gear (most efficient use of time).
    if text == "" then
        scores.content = scores.content + 1
        scores.gear = scores.gear + 1
    end
    return scores
end

--- Safely call a WoW API that may not exist or may return nil on a loading
--- screen. Returns the first return value, or `default` if the call fails.
local function safeCall(fn, default)
    local ok, v = pcall(fn)
    if ok and v ~= nil then return v end
    return default
end

--- Build the player's current context from live WoW state + saved profile.
function GM:refreshContext()
    -- Live WoW calls can return nil (e.g. before PLAYER_ENTERING_WORLD fully
    -- resolves, or on a loading screen). Guard each so a missing value never
    -- throws and breaks the whole plan.
    local class = safeCall(function() return select(2, UnitClass("player")) end, nil)
    local race = safeCall(function() return UnitRace("player") end, nil)
    local level = safeCall(function() return UnitLevel("player") end, 0)
    local ilvl = 0
    local okIlvl, avg = pcall(GetAverageItemLevel)
    if okIlvl and avg then ilvl = math.floor(select(2, avg) or 0) end
    local zone = safeCall(GetRealZoneText, "")

    -- Mythic+ rating lives deep in GetPlayerInfoByGUID's return tuple. The
    -- index has shifted across expansions, so we scan the returns for a number
    -- in the plausible rating range instead of hard-coding a fragile index.
    local ranking = 0
    local okGuid, guid = pcall(UnitGUID, "player")
    if okGuid and guid then
        local info = { pcall(GetPlayerInfoByGUID, guid) }
        if info[1] then
            for i = 2, #info do
                local v = info[i]
                if type(v) == "number" and v >= 0 and v <= 5000 then
                    ranking = v
                    break
                end
            end
        end
    end

    self.context = {
        name = safeCall(function() return UnitName("player") end, "hero"),
        class = (type(class) == "string") and class or nil,
        race = (type(race) == "string") and race or nil,
        level = level,
        ilvl = ilvl,
        zone = zone,
        mythicPlusRating = ranking,
        profile = HermesDB.playerProfile or {},
    }
    return self.context
end

--- Rank objectives by efficiency: priority up, effort down, with source bonus.
--- objective = { title, priority (1-5), effortHours, source, why, actions={} }
function GM:score(objective)
    local priority = objective.priority or 3
    local effort = math.max(objective.effortHours or 1, 0.25)
    local bonus = SOURCE_BONUS[objective.source] or 1.0
    return (priority / effort) * bonus
end

function GM:rank(objectives)
    table.sort(objectives, function(a, b) return self:score(a) > self:score(b) end)
    return objectives
end

--- The main entry point. Mirrors GameMasterEngine.plan().
--- Gathers objectives contributed by every module, ranks them, explains WHY.
--- @param text string  the player's loose question (may be "")
function GM:plan(text)
    self:refreshContext()
    local profile = self.context.profile
    local intent = self:inferIntent(text, profile)

    local objectives = {}
    -- Each module may contribute objectives for the inferred intent.
    Hermes.Registry:each(function(mod)
        if mod.contribute then
            local ok, objs = pcall(mod.contribute, mod, intent, self.context)
            if ok and objs then
                for _, o in ipairs(objs) do table.insert(objectives, o) end
            end
        end
    end)

    local ranked = self:rank(objectives)

    -- First-run friendliness: if no module contributed (e.g. the player hasn't
    -- set a profile yet), give an actionable "getting started" objective so
    -- /hermes advise is never empty.
    if #ranked == 0 then
        table.insert(ranked, {
            title = "Tell Hermes about you: /hermes profile class=<Class> spec=<Spec> content=<Raiding|PvP|Mythic+>",
            priority = 5,
            effortHours = 0.1,
            source = "learn",
            why = "Hermes reasons from your class, spec, and content focus. With that set, it ranks " ..
                  "rotation, gear, collection, and progress goals by effort-vs-reward so you always " ..
                  "know the most efficient next step.",
            actions = { "Type /hermes profile class=Warrior spec=Fury content=Mythic+", "Then /hermes advise" },
        })
    end

    -- Build a plain-language explanation (the "WHY").
    local rationale = self:explain(ranked, intent, self.context)
    return { objectives = ranked, rationale = rationale, intent = intent }
end

--- Produce a beginner-friendly explanation of the plan.
function GM:explain(ranked, intent, ctx)
    if #ranked == 0 then
        return "I don't have a specific recommendation yet. Try /hermes profile to tell me your "
            .. "favorite class, spec, and content, or ask something like 'how do I gear for Mythic+'."
    end
    local top = ranked[1]
    local lines = {}
    table.insert(lines, string.format(
        "As your Game Master, here is the most efficient path right now, %s:",
        ctx.name or "hero"))
    for i, o in ipairs(ranked) do
        table.insert(lines, string.format(
            "%d. %s  —  WHY: %s", i, o.title, o.why))
    end
    table.insert(lines, "")
    table.insert(lines, "Reasoning: " .. (top.why or "optimize effort vs. reward"))
    return table.concat(lines, "\n")
end

--- Slash-friendly advisor: print the plan to chat.
function GM:advise(text)
    local plan = self:plan(text)
    Hermes:Print(plan.rationale)
    if Hermes.CoachFrame then Hermes.CoachFrame:ShowPlan(plan) end
end

--- Update the saved player profile (mirrors PUT /profile on the backend).
function GM:setProfile(arg)
    arg = arg or ""
    -- Accept "class=Warrior spec=Fury content=Raiding"
    local profile = HermesDB.playerProfile
    local c = arg:match("class=([%w]+)")
    local s = arg:match("spec=([%w]+)")
    local co = arg:match("content=([%w+]+)")
    if c then profile.favoriteClass = c end
    if s then profile.favoriteSpec = s end
    if co then profile.favoriteContent = co end
    Hermes:Print(string.format("Profile saved: class=%s spec=%s content=%s",
        profile.favoriteClass or "?", profile.favoriteSpec or "?", profile.favoriteContent or "?"))
    self:refreshContext()
end
