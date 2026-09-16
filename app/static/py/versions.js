// What the browser's Python runs — shared by the worker (worker.js) and the page
// (schema.js), which tags every compiled grid schema it caches with
// COMPILER_VERSION and recompiles one tagged with anything else.

// ponytail: Pyodide from the CDN, pinned; self-host under /static/py/ if offline use matters.
export const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";

// linkml declares these but the app never imports them (ShEx parsing, file
// watching, a test plugin), and none has a pure-Python wheel. Registering them
// as already installed lets micropip resolve the rest.
export const MOCKED_PACKAGES = [
  ["watchdog", "6.0.0"], ["antlr4-python3-runtime", "4.9.3"], ["pytest-logging", "2015.11.4"], ["cfgraph", "0.2.1"],
];

// Versions match uv.lock, so the browser runs what the Python tests ran.
// Pyodide supplies lxml, pydantic, PyYAML, httpx and jsonschema itself.
export const PYPI_REQUIREMENTS = ["linkml==1.11.1", "linkml-runtime==1.11.1", "typer==0.26.8", "pydantic-settings==2.14.2"];

// Bump with anything that changes compile_for_grid's output (linkml-runtime,
// linkml-lib, schema_service's class adaptation).
export const COMPILER_VERSION = "linkml-runtime==1.11.1;linkml-lib==0.1.0;1";
