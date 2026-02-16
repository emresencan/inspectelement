from inspectelement.import_parser import ensure_java_imports


def test_duplicate_import_prevention_and_alphabetical_order() -> None:
    source = """package com.turkcell.pages;

import com.zeta.B;
import com.alpha.A;
import com.zeta.B;

public class FolderPage extends BaseLibrary {
}
"""
    updated = ensure_java_imports(
        source,
        required_imports=[
            "com.alpha.A",
            "com.beta.C",
        ],
    )
    assert "import com.alpha.A;\nimport com.beta.C;\nimport com.zeta.B;" in updated
    assert updated.count("import com.zeta.B;") == 1


def test_static_import_coexistence() -> None:
    source = """package com.turkcell.pages;

import static org.junit.Assert.assertTrue;
import com.turkcell.common.BaseLibrary;

public class FolderPage extends BaseLibrary {
}
"""
    updated = ensure_java_imports(
        source,
        required_imports=[
            "java.time.Duration",
            "com.turkcell.common.components.table.HtmlTableVerifier",
        ],
    )
    assert "import com.turkcell.common.BaseLibrary;" in updated
    assert "import com.turkcell.common.components.table.HtmlTableVerifier;" in updated
    assert "import java.time.Duration;" in updated
    assert "\nimport static org.junit.Assert.assertTrue;" in updated


def test_no_import_block_present_inserts_after_package() -> None:
    source = """package com.turkcell.pages;

public class FolderPage extends BaseLibrary {
}
"""
    updated = ensure_java_imports(source, ["java.time.Duration"])
    assert "package com.turkcell.pages;\n\nimport java.time.Duration;\n\npublic class FolderPage" in updated


def test_multiple_blank_lines_normalized() -> None:
    source = """package com.turkcell.pages;


import com.zeta.B;


import com.alpha.A;


public class FolderPage extends BaseLibrary {
}
"""
    updated = ensure_java_imports(source, ["com.beta.C"])
    assert "import com.alpha.A;\nimport com.beta.C;\nimport com.zeta.B;" in updated
    assert "\n\n\n" not in updated
