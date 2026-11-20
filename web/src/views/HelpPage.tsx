import { useState } from "react";

import { s } from "../strings";

/** The guide step by step, how the app works, what it keeps private, the editor's controls, and the credits the
 * models' licences ask for. */
export function HelpPage({ version }: { version: string | null }) {
  const S = s();
  const H = S.help;
  const A = H.about;
  const [topicId, setTopicId] = useState(H.guide.topics[0]?.id ?? "");
  const topic = H.guide.topics.find((t) => t.id === topicId) ?? H.guide.topics[0];
  return (
    <article className="help" aria-label={H.title}>
      <h1 className="page-title">{H.title}</h1>

      <section className="help-part help-guide">
        <h2>{H.guide.title}</h2>
        <p>{H.guide.intro}</p>
        <div className="guide">
          <nav className="guide-topics" aria-label={H.guide.title}>
            {H.guide.topics.map((t, i) => (
              <button
                key={t.id}
                type="button"
                className={`guide-topic-btn${t.id === topic?.id ? " guide-topic-on" : ""}`}
                aria-current={t.id === topic?.id ? "true" : undefined}
                onClick={() => setTopicId(t.id)}
              >
                <span className="guide-topic-n">{i + 1}</span>
                {t.title}
              </button>
            ))}
          </nav>
          {topic && (
            <div className="guide-topic" aria-live="polite">
              <h3 className="guide-topic-title">{topic.title}</h3>
              <ol className="guide-steps">
                {topic.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            </div>
          )}
        </div>
      </section>

      <section className="help-part">
        <h2>{H.how.title}</h2>
        <ol className="help-steps">
          {H.how.steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      </section>

      <section className="help-part">
        <h2>{H.inside.title}</h2>
        <ol className="help-steps">
          {H.inside.steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      </section>

      <section className="help-part">
        <h2>{H.rules}</h2>
        <p>{S.infos.presets}</p>
      </section>

      <section className="help-part">
        <h2>{H.formats}</h2>
        <p>{S.infos.formats}</p>
      </section>

      <section className="help-part">
        <h2>{H.terms.title}</h2>
        <p>{H.terms.text}</p>
      </section>

      <section className="help-part">
        <h2>{H.privacy.title}</h2>
        {H.privacy.groups.map((group) => (
          <div key={group.title} className="help-group">
            <h3>{group.title}</h3>
            <ul>
              {group.points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      <section className="help-part">
        <h2>{H.keys}</h2>
        <dl className="keys">
          {S.editor.keyList.map(([k, what]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{what}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="help-part" id="about">
        <h2>{A.title}</h2>
        <p>
          <strong>{S.appName}</strong>
          {version && <span className="about-version">{A.version(version)}</span>}
        </p>
        <p>{A.what}</p>
        <p className="about-author">
          {A.madeBy}: <strong>{A.author}</strong> ·{" "}
          {/* a window of its own (the app's window stays); no referrer, so the site never sees the app's address */}
          <a href={A.site} target="_blank" rel="noopener noreferrer">
            {A.siteName}
          </a>
        </p>
        <h3>{A.creditsTitle}</h3>
        {A.credits.map((c) => (
          <dl key={c.name} className="credit">
            <dt>{c.what}</dt>
            <dd>
              <strong>{c.name}</strong> · {c.by}
              <br />
              <span className="credit-url">{c.source}</span>
            </dd>
            <dt>{A.licence}</dt>
            <dd className="credit-url">{c.licence}</dd>
            <dt>{A.changes}</dt>
            <dd>{c.changes}</dd>
          </dl>
        ))}
        <p className="field-note">{A.download}</p>
      </section>
    </article>
  );
}
