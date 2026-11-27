"""Geometry(triangles=True): the triangle-collecting mode the print-object-analyzer skill reads meshes with."""
import unittest

import bss_fixtures as fx
import bambu_common as bc

MAIN = "3D/3dmodel.model"
TRIS = '<triangle v1="0" v2="1" v3="2"/><triangle v1="0" v2="2" v3="3"/>'


def mesh(tris=TRIS, verts=fx.CUBE_VERTS):
    vs = "".join('<vertex x="%s" y="%s" z="%s"/>' % v for v in verts)
    return "<mesh><vertices>%s</vertices><triangles>%s</triangles></mesh>" % (vs, tris)


def comp(oid, transform=None, times=1):
    return "<components>%s</components>" % ('<component objectid="%s"%s/>' % (
        oid, (' transform="%s"' % transform) if transform else "") * times)


class GeometryTrianglesTest(unittest.TestCase):
    def test_collects_triangles_through_components(self):
        g = bc.Geometry({MAIN: fx.model_xml([("1", mesh()), ("2", comp("1", "1 0 0 0 1 0 0 0 1 5 0 0"))], [])},
                        triangles=True)
        out = g.placed_meshes(MAIN, "2", bc.tf(None))
        self.assertEqual(len(out), 1)
        fn, oid, M, vs, ts = out[0]
        self.assertEqual((fn, oid), (bc.norm_name(MAIN), "1"))
        self.assertEqual(list(ts), [0, 1, 2, 0, 2, 3])
        self.assertEqual(len(vs), 24)
        self.assertEqual(bc.ap(M, (0, 0, 0)), (5, 0, 0))

    def test_bbox_mode_unchanged(self):
        g = bc.Geometry({MAIN: fx.model_xml([("1", mesh())], [])})
        self.assertEqual(g.tris, {})
        self.assertEqual(g.bbox(MAIN, "1", bc.tf(None)), [0, 10, 0, 10, 0, 10])
        with self.assertRaises(bc.BambuError):
            g.placed_meshes(MAIN, "1", bc.tf(None))

    def test_object_names_kept(self):
        model = fx.model_xml([("1", mesh())], []).replace('<object id="1"', '<object id="1" name="Lid &amp; Box"')
        g = bc.Geometry({MAIN: model}, triangles=True)
        self.assertEqual(g.names[(bc.norm_name(MAIN), "1")], "Lid & Box")

    def test_duplicate_object_id_refused(self):
        with self.assertRaises(bc.GeometryError):
            bc.Geometry({MAIN: fx.model_xml([("1", mesh()), ("1", mesh())], [])}, triangles=True)

    def test_index_past_vertices_refused(self):
        with self.assertRaises(bc.GeometryError) as cm:
            bc.Geometry({MAIN: fx.model_xml([("1", mesh('<triangle v1="0" v2="1" v3="8"/>'))], [])},
                        triangles=True)
        self.assertIn("past its vertices", str(cm.exception))
        with self.assertRaises(bc.GeometryError):
            bc.Geometry({MAIN: fx.model_xml([("1", mesh('<triangle v1="-1" v2="1" v3="2"/>'))], [])},
                        triangles=True)

    def test_non_integer_index_refused(self):
        for t in ('<triangle v1="0" v2="1"/>', '<triangle v1="0" v2="1" v3="2.5"/>',
                  '<triangle v1="a" v2="1" v3="2"/>'):
            with self.assertRaises(bc.GeometryError):
                bc.Geometry({MAIN: fx.model_xml([("1", mesh(t))], [])}, triangles=True)

    def test_stored_triangle_budget(self):
        with self.assertRaises(bc.GeometryError) as cm:
            bc.Geometry({MAIN: fx.model_xml([("1", mesh(TRIS * 3))], [])}, triangles=True, max_triangles=5)
        self.assertIn("more than 5 triangles", str(cm.exception))

    def test_placed_triangle_budget(self):
        g = bc.Geometry({MAIN: fx.model_xml([("1", mesh()), ("2", comp("1", times=3))], [])},
                        triangles=True, max_triangles=5)
        with self.assertRaises(bc.GeometryError) as cm:
            g.placed_meshes(MAIN, "2", bc.tf(None))
        self.assertIn("to place", str(cm.exception))

    def test_cycle_depth_and_visit_limits_kept(self):
        g = bc.Geometry({MAIN: fx.model_xml([("1", comp("1"))], [])}, triangles=True)
        with self.assertRaises(bc.GeometryError) as cm:
            g.placed_meshes(MAIN, "1", bc.tf(None))
        self.assertIn("refer back", str(cm.exception))
        objs = [("1", mesh())] + [(str(i), comp(str(i - 1))) for i in range(2, 22)]
        g = bc.Geometry({MAIN: fx.model_xml(objs, [])}, triangles=True)
        with self.assertRaises(bc.GeometryError) as cm:
            g.placed_meshes(MAIN, "21", bc.tf(None))
        self.assertIn("nested", str(cm.exception))
        objs = [("1", mesh())] + [(str(i), comp(str(i - 1), times=10)) for i in range(2, 8)]
        g = bc.Geometry({MAIN: fx.model_xml(objs, [])}, triangles=True)
        with self.assertRaises(bc.GeometryError) as cm:
            g.placed_meshes(MAIN, "7", bc.tf(None))
        self.assertIn("component uses", str(cm.exception))

    def test_dtd_refused(self):
        model = fx.model_xml([("1", mesh())], []).replace(
            "<model ", '<!DOCTYPE model [<!ENTITY a "aaaa">]><model ', 1)
        with self.assertRaises(bc.GeometryError):
            bc.Geometry({MAIN: model}, triangles=True)


if __name__ == "__main__":
    unittest.main()
