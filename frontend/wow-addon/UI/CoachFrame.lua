-- UI/CoachFrame.lua
-- Drives the coach panel defined in CoachFrame.xml. This is the in-game "Hermes
-- explains WHY" surface. Mirrors how the backend serves a ReasonResponse with a
-- ranked plan + explanation; here we render it in a movable frame.

local L = Hermes.L or {}

local CoachFrame = CreateFrame("Frame")
-- Bind to the XML-defined frame.
CoachFrame.frame = HermesCoachFrame
Hermes.CoachFrame = CoachFrame

function CoachFrame:ShowPlan(plan)
    local f = self.frame
    if not f then return end
    local body = f:GetName() and _G[f:GetName() .. "Body"]
    local text = ""
    if plan and plan.rationale then
        text = plan.rationale
    else
        text = L["no_plan"] or "No recommendation yet."
    end
    if body then body:SetText(text) end
    f:Show()
end

--- Live cooldown line, called by Modules/Cooldowns.lua on its 0.5s tick.
function CoachFrame:UpdateCooldowns(list)
    local f = self.frame
    if not f or not f:IsShown() then return end
    local txt = _G[f:GetName() .. "CooldownText"]
    if not txt then return end
    local parts = {}
    for _, c in ipairs(list or {}) do
        local label = c.ready and (L["rotation_ready"] or "READY")
                             or string.format("%.0fs", c.remaining or 0)
        table.insert(parts, c.name .. ": " .. label)
    end
    txt:SetText((L["cooldowns_header"] or "Cooldowns") .. ": " .. (table.concat(parts, "  |  ")))
end

function CoachFrame:IsShown()
    return self.frame and self.frame:IsShown()
end

function CoachFrame:Show()
    if self.frame then self.frame:Show() end
end

function CoachFrame:Hide()
    if self.frame then self.frame:Hide() end
end
