/-! Model of `proxy.to_openai` (src/proxy.py), the tool-call part. OpenAI-style providers answer 400 when a `tool` message does not follow an
    assistant message that announced its id, and the router would then wrongly blacklist a good model. So the translation must keep
    every tool id, in order, and put the tool messages straight after the assistant message. tests/test_proxy.py checks the same on the real code. -/
namespace Translate

inductive Blk | text | use (id : Nat) | res (id : Nat)
inductive Out | user | asst (calls : List Nat) | tool (id : Nat)

def isText : Blk → Bool | .text => true | _ => false
def useIds (bs : List Blk) : List Nat := bs.filterMap fun | .use i => some i | _ => none
def resIds (bs : List Blk) : List Nat := bs.filterMap fun | .res i => some i | _ => none

/-- assistant turn: one message, with its calls, if there is any text or any call (the Python's `if text or calls`). -/
def trAssistant (bs : List Blk) : List Out :=
  if bs.any isText || !(useIds bs).isEmpty then [.asst (useIds bs)] else []

/-- user turn: one `tool` message per tool_result in order, then the user's text if there is any. -/
def trUser (bs : List Blk) : List Out :=
  (resIds bs).map Out.tool ++ (if bs.any isText then [.user] else [])

def toolIds : List Out → List Nat := List.filterMap fun | .tool i => some i | _ => none

theorem toolIds_map (l : List Nat) : toolIds (l.map Out.tool) = l := by
  induction l with
  | nil => rfl
  | cons x xs ih => simpa [toolIds] using ih

/-- every tool_result id survives, once, in the same order. -/
theorem user_keeps_ids (bs : List Blk) : toolIds (trUser bs) = resIds bs := by
  unfold trUser
  rw [toolIds, List.filterMap_append]
  have h1 : List.filterMap (fun o => match o with | .tool i => some i | _ => none) ((resIds bs).map Out.tool) = resIds bs := by
    simpa [toolIds] using toolIds_map (resIds bs)
  rw [h1]
  split <;> simp

/-- the tool messages come first: after the first non-tool message there is no tool message (the user's text is last). -/
theorem user_tools_first (bs : List Blk) : ∃ (ts : List Nat) (tail : List Out), trUser bs = ts.map Out.tool ++ tail ∧ (∀ o ∈ tail, ∀ i, o ≠ Out.tool i) := by
  refine ⟨resIds bs, if bs.any isText then [Out.user] else [], rfl, ?_⟩
  intro o ho i
  split at ho <;> simp_all

/-- an assistant message, when emitted, announces exactly the tool_use ids of that turn. -/
theorem assistant_announces (bs : List Blk) (cs : List Nat) (h : Out.asst cs ∈ trAssistant bs) : cs = useIds bs := by
  unfold trAssistant at h
  split at h <;> simp_all

/-- the pair that matters: if every tool_result answers a tool_use of the turn before (the Anthropic API already demands this),
    then in the translated conversation every `tool` message answers a call announced by the assistant message right before it. -/
theorem pair_is_wellformed (a u : List Blk) (hwf : ∀ i ∈ resIds u, i ∈ useIds a) :
    ∃ cs, trAssistant a ++ trUser u = (if (resIds u).isEmpty then trAssistant a else [Out.asst cs]) ++ trUser u ∧
      ∀ i ∈ toolIds (trUser u), (resIds u).isEmpty = false → i ∈ cs ∧ cs = useIds a := by
  refine ⟨useIds a, ?_, ?_⟩
  · by_cases h : (resIds u).isEmpty
    · simp [h]
    · have hne : ∃ i, i ∈ resIds u := by
        cases hl : resIds u with
        | nil => simp [hl] at h
        | cons x xs => exact ⟨x, by simp⟩
      obtain ⟨i, hi⟩ := hne
      have hu : (useIds a).isEmpty = false := by
        have := hwf i hi
        cases hl : useIds a with
        | nil => simp [hl] at this
        | cons y ys => rfl
      have : trAssistant a = [Out.asst (useIds a)] := by
        unfold trAssistant; simp [hu]
      simp [h, this]
  · intro i hi _
    rw [user_keeps_ids] at hi
    exact ⟨hwf i hi, rfl⟩

end Translate

#print axioms Translate.user_keeps_ids
#print axioms Translate.user_tools_first
#print axioms Translate.assistant_announces
#print axioms Translate.pair_is_wellformed
