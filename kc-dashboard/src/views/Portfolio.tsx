import { PortfolioWidget } from "../components/PortfolioWidget";

export default function Portfolio() {
  return (
    <div className="p-8 max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Portfolio</h1>
      <PortfolioWidget />
    </div>
  );
}
