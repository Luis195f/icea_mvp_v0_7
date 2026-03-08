"use client";

import React from "react";
import * as d3 from "d3";

type Edge = [string, string];

type Props = {
  edges: Edge[];
};

type NodeDatum = { id: string };

export default function CausalDag({ edges }: Props) {
  const ref = React.useRef<SVGSVGElement | null>(null);

  React.useEffect(() => {
    const svgEl = ref.current;
    if (!svgEl) return;

    // Clear previous render (React+D3 safety)
    const svg = d3.select(svgEl);
    svg.selectAll("*").remove();

    const width = svgEl.clientWidth || 520;
    const height = 320;

    svg.attr("viewBox", `0 0 ${width} ${height}`);

    const nodesMap = new Map<string, NodeDatum>();
    edges.forEach(([a, b]) => {
      if (!nodesMap.has(a)) nodesMap.set(a, { id: a });
      if (!nodesMap.has(b)) nodesMap.set(b, { id: b });
    });

    const nodes = Array.from(nodesMap.values());
    const links = edges.map(([source, target]) => ({ source, target }));

    // Marker for arrowheads
    svg
      .append("defs")
      .append("marker")
      .attr("id", "arrow")
      .attr("viewBox", "0 -5 10 10")
      .attr("refX", 14)
      .attr("refY", 0)
      .attr("markerWidth", 6)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,-5L10,0L0,5");

    const g = svg.append("g");

    const link = g
      .append("g")
      .attr("stroke", "rgba(0,0,0,0.35)")
      .attr("stroke-width", 2)
      .selectAll("line")
      .data(links)
      .enter()
      .append("line")
      .attr("marker-end", "url(#arrow)");

    const node = g
      .append("g")
      .selectAll("g")
      .data(nodes)
      .enter()
      .append("g")
      .call(
        d3
          .drag<SVGGElement, NodeDatum>()
          .on("start", (event) => {
            if (!event.active) sim.alphaTarget(0.3).restart();
            (event.subject as unknown as { fx?: number; fy?: number }).fx = event.subject["x"];
            (event.subject as unknown as { fx?: number; fy?: number }).fy = event.subject["y"];
          })
          .on("drag", (event) => {
            (event.subject as unknown as { fx?: number; fy?: number }).fx = event.x;
            (event.subject as unknown as { fx?: number; fy?: number }).fy = event.y;
          })
          .on("end", (event) => {
            if (!event.active) sim.alphaTarget(0);
            (event.subject as unknown as { fx?: number; fy?: number }).fx = undefined;
            (event.subject as unknown as { fx?: number; fy?: number }).fy = undefined;
          })
      );

    node
      .append("circle")
      .attr("r", 18)
      .attr("fill", "white")
      .attr("stroke", "rgba(0,0,0,0.55)")
      .attr("stroke-width", 2);

    node
      .append("text")
      .text((d) => d.id)
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "middle")
      .attr("font-size", 10)
      .attr("fill", "rgba(0,0,0,0.85)");

    const sim = d3
      .forceSimulation(nodes as unknown as d3.SimulationNodeDatum[])
      .force("link", d3.forceLink(links).id((d: unknown) => (d as { id: string }).id).distance(120))
      .force("charge", d3.forceManyBody().strength(-260))
      .force("center", d3.forceCenter(width / 2, height / 2));

    sim.on("tick", () => {
      link
        .attr("x1", (d) => (d.source as unknown as { x: number }).x)
        .attr("y1", (d) => (d.source as unknown as { y: number }).y)
        .attr("x2", (d) => (d.target as unknown as { x: number }).x)
        .attr("y2", (d) => (d.target as unknown as { y: number }).y);

      node.attr("transform", (d) => {
        const x = (d as unknown as { x: number }).x;
        const y = (d as unknown as { y: number }).y;
        return `translate(${x},${y})`;
      });
    });

    // Zoom/pan for progressive disclosure
    svg.call(
      d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.6, 2.2]).on("zoom", (event) => {
        g.attr("transform", event.transform.toString());
      })
    );

    return () => {
      sim.stop();
    };
  }, [edges]);

  return (
    <div className="w-full rounded-icea border border-neutral-200 bg-white p-2">
      <svg ref={ref} className="w-full h-[320px]" aria-label="DAG causal (interactivo)" role="img" />
      <div className="mt-2 text-xs text-neutral-600">
        Arrastra nodos para reordenar. Usa zoom (rueda/trackpad). Esto soporta divulgación progresiva.
      </div>
    </div>
  );
}
