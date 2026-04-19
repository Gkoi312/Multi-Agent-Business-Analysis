import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { RequireAuth } from "../components/RequireAuth";

export function DashboardPage() {
  const navigate = useNavigate();
  const [companyName, setCompanyName] = useState("");
  const [focus, setFocus] = useState("");
  const [targetRole, setTargetRole] = useState("");
  const [industryPack, setIndustryPack] = useState("");
  const [skillPacks, setSkillPacks] = useState<string[]>([]);
  const [packsLoading, setPacksLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { items } = await api.listSkillPacks();
        if (!cancelled) {
          setSkillPacks(items);
          setError("");
        }
      } catch {
        if (!cancelled) {
          setError("Could not load skill packs. Ensure the API is running and backend/skills contains pack folders.");
        }
      } finally {
        if (!cancelled) {
          setPacksLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const { task } = await api.createReport({
        company_name: companyName,
        focus,
        target_role: targetRole,
        max_analysts: 3,
        industry_pack: industryPack,
      });
      navigate(`/tasks/${task.id}`);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to create task");
    } finally {
      setSubmitting(false);
    }
  };

  const noPacksConfigured = !packsLoading && skillPacks.length === 0;

  return (
    <RequireAuth>
      <section className="panel">
        <div className="section-header">
          <div>
            <h1>Create due diligence task</h1>
            <p className="muted">
              The run starts after you submit. Pick the company type (skill pack) from the list—options match
              subfolders under <code>backend/skills</code> that contain <code>skill_pack.yaml</code>.
            </p>
          </div>
          <div className="button-row">
            <Link className="secondary-button link-button" to="/tasks">
              All tasks
            </Link>
          </div>
        </div>
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            Company name
            <input
              onChange={(event) => setCompanyName(event.target.value)}
              required
              value={companyName}
            />
          </label>
          <label>
            Focus areas
            <textarea
              onChange={(event) => setFocus(event.target.value)}
              rows={4}
              value={focus}
            />
          </label>
          <label>
            Target role
            <input
              onChange={(event) => setTargetRole(event.target.value)}
              value={targetRole}
            />
          </label>
          <label>
            Company type (skill pack)
            <select
              disabled={packsLoading || noPacksConfigured}
              onChange={(event) => setIndustryPack(event.target.value)}
              required
              value={industryPack}
            >
              <option disabled hidden value="">
                {packsLoading ? "Loading…" : "Select…"}
              </option>
              {skillPacks.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </label>
          {noPacksConfigured ? (
            <p className="error-text">
              No skill packs available. Add a subdirectory under backend/skills with skill_pack.yaml, then refresh.
            </p>
          ) : null}
          {error ? <p className="error-text">{error}</p> : null}
          <button
            className="primary-button"
            disabled={submitting || packsLoading || noPacksConfigured}
            type="submit"
          >
            {submitting ? "Starting…" : "Generate report"}
          </button>
        </form>
      </section>
    </RequireAuth>
  );
}
