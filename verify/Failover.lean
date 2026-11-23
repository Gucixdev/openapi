/-! Model of `Router._failover` (src/router.py): walk the plan in order, return the first provider that answers.
    `t p = none` stands for every way a provider is skipped (resting, dead key, 402/429/5xx, no models, no tool support);
    `t p = some r` is an answer. The Python is tied to this by tests/test_router.py (same properties, fake clients). -/
namespace Failover

def run {α β : Type} (t : α → Option β) : List α → List α × Option (α × β)
  | [] => ([], none)
  | p :: ps =>
    match t p with
    | some r => ([], some (p, r))
    | none =>
      match run t ps with
      | (sk, res) => (p :: sk, res)

/-- An answer comes from the FIRST provider that answers: everything before it was skipped, it answered, nothing after it was called. -/
theorem answer_is_first {α β : Type} (t : α → Option β) :
    ∀ (plan sk : List α) (p : α) (r : β), run t plan = (sk, some (p, r)) →
      (∃ rest, plan = sk ++ p :: rest) ∧ t p = some r ∧ ∀ q ∈ sk, t q = none := by
  intro plan
  induction plan with
  | nil => intro sk p r h; simp [run] at h
  | cons x xs ih =>
    intro sk p r h
    simp only [run] at h
    cases hx : t x with
    | some r' =>
      rw [hx] at h
      simp at h
      obtain ⟨h1, h2, h3⟩ := h
      subst h1; subst h2; subst h3
      exact ⟨⟨xs, rfl⟩, hx, by simp⟩
    | none =>
      rw [hx] at h
      cases hr : run t xs with
      | mk sk' res =>
        rw [hr] at h
        simp at h
        obtain ⟨h1, h2⟩ := h
        subst h1
        obtain ⟨⟨rest, hrest⟩, hp, hsk⟩ := ih sk' p r (by rw [hr, h2])
        refine ⟨⟨rest, by simp [hrest]⟩, hp, ?_⟩
        intro q hq
        simp at hq
        rcases hq with rfl | hq
        · exact hx
        · exact hsk q hq

/-- No answer means every provider was tried (skipped) and every one failed: nothing is silently dropped from the plan. -/
theorem none_means_all_failed {α β : Type} (t : α → Option β) :
    ∀ (plan sk : List α), run t plan = (sk, none) → sk = plan ∧ ∀ q ∈ plan, t q = none := by
  intro plan
  induction plan with
  | nil => intro sk h; simp [run] at h; simp [h]
  | cons x xs ih =>
    intro sk h
    simp only [run] at h
    cases hx : t x with
    | some r' => rw [hx] at h; simp at h
    | none =>
      rw [hx] at h
      cases hr : run t xs with
      | mk sk' res =>
        rw [hr] at h
        simp at h
        obtain ⟨h1, h2⟩ := h
        subst h1
        obtain ⟨e, hall⟩ := ih sk' (by rw [hr, h2])
        refine ⟨by simp [e], ?_⟩
        intro q hq
        simp at hq
        rcases hq with rfl | hq
        · exact hx
        · exact hall q hq

/-- The skipped list never exceeds the plan, so the loop is bounded by the number of providers (and the Python `for` over `plan` terminates). -/
theorem skipped_le {α β : Type} (t : α → Option β) :
    ∀ (plan : List α), (run t plan).1.length ≤ plan.length := by
  intro plan
  induction plan with
  | nil => simp [run]
  | cons x xs ih =>
    simp only [run]
    cases hx : t x with
    | some r => simp
    | none =>
      cases hr : run t xs with
      | mk sk res =>
        simp
        have := ih
        rw [hr] at this
        simpa using this

end Failover

#print axioms Failover.answer_is_first
#print axioms Failover.none_means_all_failed
#print axioms Failover.skipped_le
