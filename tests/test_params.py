"""User parameters: expression core, live field bindings, persistence."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from leathercad.expr import evaluate, names_in, resolve, valid_name  # noqa: E402


# -- expression core -----------------------------------------------------------
def test_evaluate_with_names_and_units():
    vals = {"strap_w": 20.0, "n": 4.0}
    assert evaluate("strap_w*2+5", vals) == 45.0
    assert evaluate("strap_w + 1in", vals) == 45.4
    assert evaluate("105/2 + 3") == 55.5                 # plain math still works
    with pytest.raises(ValueError):
        evaluate("unknown_thing + 1", vals)
    with pytest.raises(Exception):
        evaluate("__import__('os')", vals)               # no calls, ever


def test_unit_suffix_does_not_mangle_identifiers():
    vals = {"strap2in": 7.0}
    assert evaluate("strap2in", vals) == 7.0             # '2in' inside a name
    assert names_in("strap_w*2 + 3in") == {"strap_w"}    # unit word excluded


def test_resolve_chain_and_cycle():
    vals = resolve({"a": "10", "b": "a*2", "c": "b + a"})
    assert vals == {"a": 10.0, "b": 20.0, "c": 30.0}
    # definition order doesn't matter
    vals = resolve({"b": "a*2", "a": "10"})
    assert vals["b"] == 20.0
    with pytest.raises(ValueError, match="cycle"):
        resolve({"a": "b", "b": "a"})
    with pytest.raises(ValueError):
        resolve({"a": "nope + 1"})


def test_valid_name():
    assert valid_name("strap_w") and valid_name("W2")
    assert not valid_name("in") and not valid_name("MM")  # unit words reserved
    assert not valid_name("2w") and not valid_name("a-b") and not valid_name("")


# -- document storage ----------------------------------------------------------
def test_params_and_bindings_roundtrip(tmp_path):
    from leathercad.document import Document
    doc = Document()
    doc.params = {"strap_w": "20", "half": "strap_w/2"}
    doc.bindings = {"shape1:w": "strap_w*2"}
    p = tmp_path / "t.json"
    doc.save(str(p))
    doc2 = Document.load(str(p))
    assert doc2.params == doc.params
    assert doc2.bindings == doc.bindings
    assert doc2.param_values() == {"strap_w": 20.0, "half": 10.0}


# -- GUI plumbing ---------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _type(spin, text):
    """Simulate the user typing into a MathSpinBox and committing."""
    spin.lineEdit().setText(text)
    spin.lineEdit().textEdited.emit(text)     # what a real keystroke does
    spin.interpretText()


def test_spinbox_uses_params_and_tracks_expressions(qapp):
    from leathercad_app import mathspin
    from leathercad_app.mathspin import MathSpinBox
    mathspin.set_params_provider(lambda: {"strap_w": 20.0})
    sp = MathSpinBox()
    sp.setRange(0, 1000)
    _type(sp, "strap_w*2")
    assert sp.value() == 40.0
    assert sp.last_expr == "strap_w*2"        # a parameter was used: linked
    _type(sp, "12")
    assert sp.value() == 12.0
    assert sp.last_expr == ""                 # plain number: unlink marker
    sp.setValue(5.0)
    assert sp.last_expr is None               # programmatic load: untouched
    mathspin.set_params_provider(None)


def test_param_edit_redrives_bound_fields(qapp):
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, Transform
    from leathercad_app import mathspin
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem

    doc = Document()
    doc.params = {"strap_w": "20"}
    r = Rectangle(width=40, height=30, transform=Transform(x=0, y=0),
                  layer="Cut")
    doc.add_shape(r)
    win = MainWindow(doc)
    win.canvas.rebuild()
    it = [i for i in win.canvas.scene_obj.items()
          if isinstance(i, ShapeItem)][0]
    it.setSelected(True)
    win._selection_changed()

    # user types an expression into the Width field -> binding recorded
    _type(win.properties.w, "strap_w*2")
    win.properties._apply()
    assert r.width == 40.0
    assert doc.bindings == {f"{r.shape_id}:w": "strap_w*2"}

    # change the parameter -> the width follows
    doc.params["strap_w"] = "30"
    errors = win.canvas.apply_param_bindings()
    assert errors == []
    assert r.width == 60.0

    # typing a plain number back unlinks
    it2 = [i for i in win.canvas.scene_obj.items()
           if isinstance(i, ShapeItem)][0]
    it2.setSelected(True)
    win._selection_changed()
    _type(win.properties.w, "42")
    win.properties._apply()
    assert doc.bindings == {}
    assert r.width == 42.0
    mathspin.set_params_provider(None)


def test_bindings_survive_deleted_shape_and_bad_expr(qapp):
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, Transform
    from leathercad_app import canvas as cm

    doc = Document()
    doc.params = {"w": "50"}
    r = Rectangle(width=10, height=10, transform=Transform(x=0, y=0))
    doc.add_shape(r)
    doc.bindings = {f"{r.shape_id}:w": "w", "ghost:w": "w",
                    f"{r.shape_id}:h": "w + oops"}
    c = cm.Canvas(doc)
    c.rebuild()
    errors = c.apply_param_bindings()
    assert r.width == 50.0                     # good binding applied
    assert "ghost:w" not in doc.bindings       # dead shape pruned
    assert len(errors) == 1 and "oops" in errors[0]
