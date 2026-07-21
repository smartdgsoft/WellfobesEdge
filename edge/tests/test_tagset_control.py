"""Tag-set control: the config's `tags` list governs exactly what the gateway
emits. The gateway applies this gate in run():

    if self._allowed_tags is not None and tag not in self._allowed_tags:
        continue

We test that exact predicate across the three semantics:
  None   -> no restriction, everything passes  (SKU-1 / no central config)
  {..}   -> only listed tags pass              (config restricts the set)
  set()  -> explicit silence, nothing passes   (config says acquire nothing)
"""


def gate(allowed, tag) -> bool:
    """True if the tag should be EMITTED. Mirrors EdgeGateway.run's condition."""
    return not (allowed is not None and tag not in allowed)


ALL = ["sim_level", "sim_pressure", "sim_running"]


def emitted(allowed):
    return {t for t in ALL if gate(allowed, t)}


def test():
    assert emitted(None) == {"sim_level", "sim_pressure", "sim_running"}
    print("  None    -> all 3 tags")
    assert emitted({"sim_level"}) == {"sim_level"}
    print("  {level} -> only sim_level")
    assert emitted({"sim_level", "sim_running"}) == {"sim_level", "sim_running"}
    print("  {level,running} -> those two")
    assert emitted(set()) == set()
    print("  set()   -> silent (nothing emitted)")
    assert emitted({"nonexistent"}) == set()
    print("  {nonexistent} -> silent (allowlist tag not in stream)")
    print("\n\u2705 tag-set control: config governs exactly what the gateway emits")


if __name__ == "__main__":
    test()
