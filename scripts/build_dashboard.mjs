// Deploy only public dashboard assets; never copy Python, .env or paper data.
import { cpSync, mkdirSync } from "node:fs";
mkdirSync("web-dist/assets", { recursive: true });
cpSync("sbsm/static/index.html", "web-dist/index.html");
for (const name of ["app.js", "style.css"]) {
  cpSync(`sbsm/static/${name}`, `web-dist/assets/${name}`);
}
