/* ChatInterface — mode conversationnel [Flux.ai].
 *
 * Bulles user/assistant/system, blocs structurés (MessageBlocks), prompts
 * d'exemple, import de fichiers (POST /files/upload), envoi → création de
 * projet + lancement du pipeline, narration live via useConversation.
 */

import React, { useEffect, useRef, useState } from 'react';
import MessageBlocks from './MessageBlocks.jsx';
import { useConversation, SAMPLE_PROMPTS } from './useConversation.js';
import { api, ApiError } from '../../services/api.js';
import { useProject } from '../../context/index.js';

function formatTime(ts) {
  try {
    return new Date(ts).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

export default function ChatInterface() {
  const { messages, sendMessage, loading, error, reset, projectName, messagesEndRef } =
    useConversation();
  const [draft, setDraft] = useState('');
  const [uploadNote, setUploadNote] = useState('');
  const fileInputRef = useRef(null);
  const { projectId } = useProject();

  /* Auto-scroll en bas à chaque nouveau message. */
  useEffect(() => {
    if (messagesEndRef && messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [messages]);

  const submit = (e) => {
    e.preventDefault();
    const text = draft;
    setDraft('');
    sendMessage(text);
  };

  const onFiles = async (event) => {
    const files = event.target.files;
    if (!files || !files.length) return;
    try {
      const res = await api.uploadFiles(files);
      const count = files.length;
      setUploadNote(`${count} fichier(s) transmis à la gateway`);
      if (res && res.project_id) {
        // la gateway peut rattacher les fichiers à un projet existant
      }
    } catch (err) {
      setUploadNote(
        err instanceof ApiError ? `Import impossible : ${err.message}` : 'Import impossible (réseau)'
      );
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = '';
      setTimeout(() => setUploadNote(''), 4000);
    }
  };

  return (
    <section className="card card--chat" aria-label="Assistant de conception">
      <header className="card__header">
        <span className="card__title">Assistant de conception</span>
        <span className="badge badge--flux">Flux.ai</span>
        <span className="spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={reset} data-tip="Nouvelle conversation">
          ↺
        </button>
      </header>

      <div className="chat-msgs">
        {messages.map((msg) => (
          <div key={msg.id} className={`msg msg--${msg.role}`}>
            <div className="msg__bubble">
              {msg.status === 'pending' ? (
                <span className="typing" aria-label="L'assistant rédige">
                  <i />
                  <i />
                  <i />
                </span>
              ) : (
                <>
                  {msg.text ? <span>{msg.text}</span> : null}
                  <MessageBlocks blocks={msg.blocks} />
                </>
              )}
            </div>
            {msg.role !== 'system' ? (
              <span className="msg__meta">
                {msg.role === 'user' ? 'vous' : 'Flux.ai'} · {formatTime(msg.ts)}
              </span>
            ) : null}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-toolbar">
        <div className="prompt-chips">
          {SAMPLE_PROMPTS.map((prompt) => (
            <button
              key={prompt}
              type="button"
              className="prompt-chip"
              disabled={loading}
              onClick={() => sendMessage(prompt)}
            >
              {prompt}
            </button>
          ))}
        </div>
        {uploadNote ? <span className="muted" style={{ fontSize: 11 }}>{uploadNote}</span> : null}
        {projectName ? (
          <span className="mono muted" style={{ fontSize: 10.5 }}>
            {projectName}
          </span>
        ) : null}
      </div>

      <form className="chat-input-row" onSubmit={submit}>
        <label className="file-label" data-tip="Netlists, datasheets, gerbers">
          +
          <input
            ref={fileInputRef}
            type="file"
            multiple
            style={{ display: 'none' }}
            onChange={onFiles}
            aria-label="Importer des fichiers"
          />
        </label>
        <textarea
          className="input"
          rows={1}
          placeholder="Décrivez votre carte — ex. une carte drone STM32 + LoRa…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit(e);
            }
          }}
          aria-label="Message"
        />
        <button type="submit" className="btn btn--primary" disabled={loading || !draft.trim()}>
          Envoyer
        </button>
      </form>
      {error ? (
        <div className="row" style={{ padding: '0 12px 10px', fontSize: 11 }}>
          <span className="badge badge--warn">info</span>
          <span className="muted">{error}</span>
        </div>
      ) : null}
    </section>
  );
}
