from pkg import pipeline, support


def test_runs_every_stage():
    support.reset()
    out = pipeline.run(["a", "b"])
    assert len(out) == 2
    assert out[0].startswith("a|")
    assert len(support.SEEN) == 20


def test_manifest():
    assert len(pipeline.manifest()) == 20
    assert pipeline.manifest()[0].startswith("clean:")
