// One-off build step: simplify the `geojson-india` state outlines (npm package, fetched with
// `npm pack geojson-india@0.0.2`) into a small quantised TopoJSON committed at
// src/lib/geo/india.topo.json. Usage: node scripts/build-india-topo.mjs <india.json>
import { readFileSync, writeFileSync } from "node:fs";
import { topology } from "topojson-server";
import { presimplify, simplify, quantile } from "topojson-simplify";

const src = JSON.parse(readFileSync(process.argv[2], "utf8"));
let topo = topology({ states: src }, 1e4);
topo = presimplify(topo);
topo = simplify(topo, quantile(topo, 0.18));
// keep only the name property
for (const g of topo.objects.states.geometries) g.properties = { name: g.properties.name };
writeFileSync("src/lib/geo/india.topo.json", JSON.stringify(topo));
console.log("bytes", JSON.stringify(topo).length);
