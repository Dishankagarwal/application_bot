import React, { useState, useEffect, useRef } from 'react';

const API_BASE = 'http://localhost:8000/api';

export default function App() {
  // Session states
  const [resume, setResume] = useState(null); // { filename, charCount }
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  
  // Search parameters state
  const [searchTerm, setSearchTerm] = useState('Python Backend Developer');
  const [location, setLocation] = useState('Remote');
  const [resultsWanted, setResultsWanted] = useState(15);
  const [selectedSites, setSelectedSites] = useState(['linkedin', 'indeed', 'glassdoor', 'zip_recruiter', 'google', 'naukri', 'bayt', 'gemini_search']);
  const [jobType, setJobType] = useState('any'); // 'any', 'fulltime', 'contract'
  const [minSalary, setMinSalary] = useState('');
  const [maxSalary, setMaxSalary] = useState('');
  const [hoursOld, setHoursOld] = useState(''); // '', '168' (7 days), '720' (1 month)
  
  // Scraped Jobs state
  const [jobs, setJobs] = useState([]);
  const [hasSearched, setHasSearched] = useState(false);
  
  // Collapsed job descriptions state (map of jobId -> boolean)
  const [expandedJobs, setExpandedJobs] = useState({});
  
  // Tailoring Modal states
  const [selectedJob, setSelectedJob] = useState(null);
  const [tailorLoading, setTailorLoading] = useState(false);
  const [tailoredResume, setTailoredResume] = useState(null); // { markdown, changes: [...] }
  const [showModal, setShowModal] = useState(false);
  const [copySuccess, setCopySuccess] = useState(false);
  
  // Drag and drop states
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef(null);
  
  // WebSocket progress state trackers
  const [searchProgress, setSearchProgress] = useState({});
  const [scrapedCounts, setScrapedCounts] = useState({});
  const [scrapedCount, setScrapedCount] = useState(0);
  const [loaderMessage, setLoaderMessage] = useState('');
  const wsStateRef = useRef({ lastCount: 0, prevSite: null });

  // Check health status on load to retrieve existing session
  useEffect(() => {
    fetch(`${API_BASE}/status`)
      .then(res => res.json())
      .then(data => {
        if (data.has_resume) {
          setResume({
            filename: data.resume_filename,
            charCount: 0 // Placeholder
          });
        }
      })
      .catch(err => console.error("Could not fetch server status on load:", err));
  }, []);

  // Handle Drag Over
  const handleDragOver = (e) => {
    e.preventDefault();
    setDragOver(true);
  };

  // Handle Drag Leave
  const handleDragLeave = () => {
    setDragOver(false);
  };

  // Handle Drop File
  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      uploadFile(e.dataTransfer.files[0]);
    }
  };

  // Handle File Input Select
  const handleFileSelect = (e) => {
    if (e.target.files && e.target.files[0]) {
      uploadFile(e.target.files[0]);
    }
  };

  // Click Upload zone trigger
  const triggerFileInput = () => {
    fileInputRef.current.click();
  };

  // File Upload logic
  const uploadFile = (file) => {
    if (file.type !== 'application/pdf' && !file.name.endsWith('.pdf')) {
      setError('Please upload a valid PDF resume file.');
      return;
    }
    
    setError('');
    setLoading(true);
    
    const formData = new FormData();
    formData.append('file', file);
    
    fetch(`${API_BASE}/upload-resume`, {
      method: 'POST',
      body: formData
    })
      .then(res => {
        if (!res.ok) throw new Error("Server error parsing PDF file.");
        return res.json();
      })
      .then(data => {
        setResume({
          filename: data.filename,
          charCount: data.char_count
        });
        setLoading(false);
      })
      .catch(err => {
        console.error(err);
        setError(err.message || 'Failed to upload and parse resume.');
        setLoading(false);
      });
  };

  // Remove active resume
  const handleRemoveResume = () => {
    // Just clear local state (in production, could add clear route)
    setResume(null);
    setError('');
  };

  // Toggle Preferred Sites
  const handleToggleSite = (site) => {
    if (selectedSites.includes(site)) {
      // Keep at least one
      if (selectedSites.length > 1) {
        setSelectedSites(selectedSites.filter(s => s !== site));
      }
    } else {
      setSelectedSites([...selectedSites, site]);
    }
  };

  // Search Jobs Logic
  const handleSearchJobs = (e) => {
    e.preventDefault();
    if (!searchTerm || !searchTerm.trim()) {
      setError('Search keyword is required.');
      return;
    }
    
    setError('');
    setLoading(true);
    setJobs([]);
    setHasSearched(true);

    // Initialize progress tracking states
    const initialProgress = {};
    selectedSites.forEach(s => {
      initialProgress[s] = 'pending';
    });
    if (resume) {
      initialProgress['gemini'] = 'pending';
    }
    setSearchProgress(initialProgress);
    
    const initialCounts = {};
    selectedSites.forEach(s => {
      initialCounts[s] = 0;
    });
    setScrapedCounts(initialCounts);
    setScrapedCount(0);
    setLoaderMessage('Connecting to search gateway...');

    // Reset tracking state ref
    wsStateRef.current = { lastCount: 0, prevSite: null };

    // Establish WebSocket connection to backend
    const wsUrl = API_BASE.replace(/^http/, 'ws') + '/search-jobs-ws';
    const socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      setLoaderMessage('Connection established. Starting aggregator...');
      socket.send(JSON.stringify({
        search_term: searchTerm,
        location: location,
        results_wanted: resultsWanted,
        site_names: selectedSites,
        job_type: jobType === 'any' ? null : jobType,
        min_salary: minSalary ? parseInt(minSalary) : null,
        max_salary: maxSalary ? parseInt(maxSalary) : null,
        hours_old: hoursOld ? parseInt(hoursOld) : null
      }));
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'progress') {
          const currentSite = data.current_site;
          const currentJobsFound = data.jobs_found;
          
          setLoaderMessage(data.message);
          setScrapedCount(currentJobsFound);
          
          setSearchProgress(prev => {
            const updated = { ...prev };
            const state = wsStateRef.current;
            if (state.prevSite) {
              updated[state.prevSite] = 'completed';
            }
            updated[currentSite] = 'scraping';
            return updated;
          });
          
          const state = wsStateRef.current;
          if (state.prevSite) {
            const diff = currentJobsFound - state.lastCount;
            setScrapedCounts(prev => ({
              ...prev,
              [state.prevSite]: diff > 0 ? diff : 0
            }));
          }
          
          // Update tracking state ref
          wsStateRef.current = {
            lastCount: currentJobsFound,
            prevSite: currentSite
          };
        } else if (data.type === 'results') {
          // Finalize all remaining states
          const state = wsStateRef.current;
          setSearchProgress(prev => {
            const updated = { ...prev };
            if (state.prevSite) {
              updated[state.prevSite] = 'completed';
            }
            if (resume) {
              updated['gemini'] = 'completed';
            }
            return updated;
          });

          // Calculate final counts for last platform if appropriate
          if (state.prevSite && state.prevSite !== 'gemini') {
            const totalJobs = data.jobs ? data.jobs.length : 0;
            const diff = totalJobs - state.lastCount;
            setScrapedCounts(prev => ({
              ...prev,
              [state.prevSite]: diff > 0 ? diff : 0
            }));
          }

          setJobs(data.jobs || []);
          setLoading(false);
          socket.close();
        } else if (data.type === 'error') {
          setError(data.message);
          setLoading(false);
          socket.close();
        }
      } catch (err) {
        console.error("Failed to parse WS message:", err);
      }
    };

    socket.onerror = (err) => {
      console.error("WebSocket connection error:", err);
      setError("WebSocket connection failed. Please ensure the backend server is running.");
      setLoading(false);
    };

    socket.onclose = () => {
      setLoading(false);
    };
  };

  // Toggle Job Description Collapse
  const toggleJobDesc = (id) => {
    setExpandedJobs(prev => ({
      ...prev,
      [id]: !prev[id]
    }));
  };

  // Fetch Tailored Resume
  const handleTailorResume = (job) => {
    setSelectedJob(job);
    setTailorLoading(true);
    setShowModal(true);
    setTailoredResume(null);
    setCopySuccess(false);
    
    fetch(`${API_BASE}/tailor-resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: job.id })
    })
      .then(res => {
        if (!res.ok) throw new Error("Failed to tailor resume.");
        return res.json();
      })
      .then(data => {
        setTailoredResume({
          markdown: data.tailored_resume_markdown,
          changes: data.changes_made || []
        });
        setTailorLoading(false);
      })
      .catch(err => {
        console.error(err);
        setError('Failed to customize resume for the target job.');
        setShowModal(false);
        setTailorLoading(false);
      });
  };

  // Download Tailored PDF Action
  const handleDownloadPdf = (jobId) => {
    window.open(`${API_BASE}/download-tailored-pdf?job_id=${jobId}`, '_blank');
  };

  // Copy to Clipboard Action
  const copyToClipboard = () => {
    if (tailoredResume && tailoredResume.markdown) {
      navigator.clipboard.writeText(tailoredResume.markdown)
        .then(() => {
          setCopySuccess(true);
          setTimeout(() => setCopySuccess(false), 2000);
        })
        .catch(err => console.error("Could not copy text: ", err));
    }
  };

  // Helper to get score badge colour class
  const getScoreClass = (score) => {
    if (score >= 80) return 'high';
    if (score >= 50) return 'mid';
    return 'low';
  };

  return (
    <div className="app-container">
      {/* Header */}
      <header className="app-header">
        <div className="logo-section">
          <h1>
            JobApp Bot <span className="logo-badge">V1.0</span>
          </h1>
          <p className="logo-subtitle">AI-Powered Job Matcher & Resume Customizer (Human-in-the-Loop)</p>
        </div>
        
        <div className="resume-status-widget">
          <span className={`status-indicator ${resume ? 'success' : 'warning'}`} />
          <span style={{ fontSize: '0.875rem', fontWeight: 500 }}>
            {resume ? `Resume: ${resume.filename}` : 'No resume uploaded'}
          </span>
        </div>
      </header>

      {error && (
        <div className="error-alert">
          <span style={{ fontSize: '1.25rem' }}>⚠️</span>
          <span>{error}</span>
        </div>
      )}

      {/* Main Grid Layout */}
      <div className="main-grid">
        {/* Left column - control panel */}
        <aside className="sidebar">
          {/* Resume Upload Card */}
          <div className="panel-card">
            <h3 className="panel-title">
              <span>📄</span> Resume Upload
            </h3>
            
            {!resume ? (
              <div 
                className={`file-upload-zone ${dragOver ? 'drag-over' : ''}`}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={triggerFileInput}
              >
                <div className="file-upload-icon">📥</div>
                <p className="file-upload-text">
                  Drag & Drop PDF or <span>Browse Files</span>
                </p>
                <input 
                  type="file" 
                  ref={fileInputRef}
                  onChange={handleFileSelect}
                  className="file-input"
                  accept=".pdf"
                />
              </div>
            ) : (
              <div className="uploaded-file-info">
                <div className="file-details">
                  <span className="file-name">{resume.filename}</span>
                  {resume.charCount > 0 && (
                    <span className="file-size">{resume.charCount.toLocaleString()} parsed characters</span>
                  )}
                </div>
                <button className="btn-remove-file" onClick={handleRemoveResume} title="Remove file">
                  ✕
                </button>
              </div>
            )}
          </div>

          {/* Job Search Params Card */}
          <div className="panel-card">
            <h3 className="panel-title">
              <span>🔍</span> Search Query
            </h3>
            <form onSubmit={handleSearchJobs}>
              <div className="form-group">
                <label className="form-label">Job Title / Keyword</label>
                <input 
                  type="text" 
                  className="form-input" 
                  value={searchTerm} 
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder="e.g. React Frontend Developer"
                  required
                />
              </div>

              <div className="form-group">
                <label className="form-label">Location</label>
                <input 
                  type="text" 
                  className="form-input" 
                  value={location} 
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder="e.g. Remote or Austin, TX"
                />
              </div>

              <div className="form-group">
                <label className="form-label">Results Limit</label>
                <input 
                  type="number" 
                  className="form-input" 
                  min="1" 
                  max="50"
                  value={resultsWanted} 
                  onChange={(e) => setResultsWanted(parseInt(e.target.value) || 10)}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Job Type</label>
                <select 
                  className="form-input" 
                  value={jobType} 
                  onChange={(e) => setJobType(e.target.value)}
                >
                  <option value="any">Any Type</option>
                  <option value="fulltime">Full Time</option>
                  <option value="contract">Freelancing</option>
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">Package Range (Annual USD)</label>
                <div className="salary-inputs-row" style={{ display: 'flex', gap: '0.5rem' }}>
                  <input 
                    type="number" 
                    className="form-input" 
                    value={minSalary} 
                    onChange={(e) => setMinSalary(e.target.value)}
                    placeholder="Min"
                    min="0"
                  />
                  <input 
                    type="number" 
                    className="form-input" 
                    value={maxSalary} 
                    onChange={(e) => setMaxSalary(e.target.value)}
                    placeholder="Max"
                    min="0"
                  />
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Date Posted</label>
                <select 
                  className="form-input" 
                  value={hoursOld} 
                  onChange={(e) => setHoursOld(e.target.value)}
                >
                  <option value="">Anytime</option>
                  <option value="168">Last 7 Days</option>
                  <option value="720">Last 1 Month</option>
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">Preferred Platforms</label>
                <div className="sites-checkbox-grid">
                  {['linkedin', 'indeed', 'glassdoor', 'zip_recruiter', 'google', 'naukri', 'bayt', 'gemini_search'].map(site => (
                    <div 
                      key={site} 
                      className={`checkbox-card ${selectedSites.includes(site) ? 'selected' : ''}`}
                      onClick={() => handleToggleSite(site)}
                    >
                      <input 
                        type="checkbox" 
                        checked={selectedSites.includes(site)} 
                        onChange={() => {}} // Handle click in card parent
                      />
                      <span className="checkbox-label">{site.replace('_', ' ')}</span>
                    </div>
                  ))}
                </div>
              </div>

              <button type="submit" className="btn-primary" disabled={loading}>
                {loading ? 'Searching & Ranking...' : 'Find Matches'}
              </button>
            </form>
          </div>
        </aside>

        {/* Right column - matched results display */}
        <main className="content-area">
          {loading && jobs.length === 0 && (
            <div className="loader-container">
              {Object.keys(searchProgress).length > 0 ? (
                <div className="search-progress-dashboard" style={{ width: '100%' }}>
                  <div className="loader-header">
                    <div className="spinner" />
                    <div>
                      <h3 className="loader-title">Aggregating Job Market Listings</h3>
                      <p className="loader-subtitle">{loaderMessage || 'Establishing connection...'}</p>
                    </div>
                  </div>

                  <div className="progress-summary">
                    <div className="progress-stat">
                      <span className="stat-value">{scrapedCount}</span>
                      <span className="stat-label">Jobs Discovered</span>
                    </div>
                    <div className="progress-divider" />
                    <div className="progress-stat">
                      <span className="stat-value">
                        {selectedSites.filter(s => searchProgress[s] === 'completed' || searchProgress[s] === 'failed').length} / {selectedSites.length}
                      </span>
                      <span className="stat-label">Platforms Queried</span>
                    </div>
                  </div>

                  <div className="platforms-progress-list">
                    {selectedSites.map(site => {
                      const status = searchProgress[site] || 'pending';
                      const count = scrapedCounts[site] || 0;
                      return (
                        <div key={site} className={`platform-progress-item ${status}`}>
                          <div className="platform-info">
                            <span className={`platform-indicator-dot ${status}`} />
                            <span className="platform-name">{site.replace('_', ' ')}</span>
                          </div>
                          <span className="platform-status-text">
                            {status === 'pending' && 'Pending'}
                            {status === 'scraping' && 'Scraping...'}
                            {status === 'completed' && `${count} jobs`}
                            {status === 'failed' && 'Failed'}
                          </span>
                        </div>
                      );
                    })}

                    {resume && (
                      <div className={`platform-progress-item ${searchProgress['gemini'] || 'pending'}`}>
                        <div className="platform-info">
                          <span className={`platform-indicator-dot ${searchProgress['gemini'] || 'pending'}`} />
                          <span className="platform-name">Gemini Fit Ranking</span>
                        </div>
                        <span className="platform-status-text">
                          {(searchProgress['gemini'] || 'pending') === 'pending' && 'Pending'}
                          {searchProgress['gemini'] === 'scraping' && 'Analyzing...'}
                          {searchProgress['gemini'] === 'completed' && 'Completed'}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <>
                  <div className="spinner" />
                  <p className="loader-text">Analyzing resume structure and visual styling...</p>
                </>
              )}
            </div>
          )}

          {!loading && jobs.length === 0 && !hasSearched && (
            <div className="empty-state">
              <div className="empty-state-icon">🤖</div>
              <h3>Start Your Job Match Search</h3>
              <p>Upload your PDF resume, fill in target criteria, and click "Find Matches" to search top job boards and review tailored resume suggestions.</p>
            </div>
          )}

          {!loading && jobs.length === 0 && hasSearched && (
            <div className="empty-state">
              <div className="empty-state-icon">🚫</div>
              <h3>No Jobs Found</h3>
              <p>Try modifying your search criteria, widening the search location, or choosing additional job platforms.</p>
            </div>
          )}

          {jobs.length > 0 && (
            <>
              <div className="results-header">
                <h2 className="results-count">
                  Scraped & Match-Ranked <span>{jobs.length}</span> postings
                </h2>
              </div>

              <div className="jobs-list">
                {jobs.map((job) => {
                  const isExpanded = expandedJobs[job.id] || false;
                  return (
                    <article key={job.id} className="job-card">
                      <div className="job-card-header">
                        <div className="job-main-info">
                          <h3 className="job-title">{job.title}</h3>
                          <div className="job-company">
                            🏢 {job.company}
                          </div>
                          <div className="job-meta-row">
                            <div className="job-meta-item">📍 {job.location}</div>
                            {job.salary && <div className="job-meta-item">💵 {job.salary}</div>}
                            <div className="job-meta-item">🌐 Source: {job.site}</div>
                            {job.job_type && <div className="job-meta-item">💼 {job.job_type}</div>}
                          </div>
                        </div>

                        {/* Match Score Display */}
                        {resume ? (
                          <div className="match-score-container">
                            <span className={`match-score-value ${getScoreClass(job.match_score)}`}>
                              {job.match_score}%
                            </span>
                            <span className="match-score-label">Fit Score</span>
                          </div>
                        ) : (
                          <div className="match-score-container" style={{ opacity: 0.5 }} title="Upload resume to view score">
                            <span className="match-score-value" style={{ fontSize: '1rem', color: 'var(--color-text-muted)' }}>
                              N/A
                            </span>
                            <span className="match-score-label">No Resume</span>
                          </div>
                        )}
                      </div>

                      {/* Score description details */}
                      {resume && job.match_summary && (
                        <div className="match-summary-box">
                          {job.match_summary}
                        </div>
                      )}

                      {/* Keywords tagging */}
                      {resume && ((job.matched_keywords && job.matched_keywords.length > 0) || (job.missing_keywords && job.missing_keywords.length > 0)) && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                          {job.matched_keywords && job.matched_keywords.length > 0 && (
                            <div className="keywords-section">
                              <span className="keyword-label">Matching Skills</span>
                              <div className="tags-container">
                                {job.matched_keywords.map((kw, i) => (
                                  <span key={i} className="tag matched">{kw}</span>
                                ))}
                              </div>
                            </div>
                          )}

                          {job.missing_keywords && job.missing_keywords.length > 0 && (
                            <div className="keywords-section">
                              <span className="keyword-label">Missing Skills / Gaps</span>
                              <div className="tags-container">
                                {job.missing_keywords.map((kw, i) => (
                                  <span key={i} className="tag missing">{kw}</span>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      )}

                      {/* Job description preview collapsible */}
                      {job.description && (
                        <div className="job-desc-collapsible">
                          <button className="desc-toggle-btn" onClick={() => toggleJobDesc(job.id)}>
                            {isExpanded ? 'Collapse description ▴' : 'Expand full description ▾'}
                          </button>
                          
                          {isExpanded ? (
                            <div className="job-desc-preview">
                              {job.description}
                            </div>
                          ) : (
                            <div className="job-desc-preview" style={{ display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                              {job.description}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Action buttons */}
                      <div className="card-actions-row">
                        <button 
                          className="btn-secondary" 
                          onClick={() => handleTailorResume(job)}
                          disabled={!resume}
                          title={!resume ? "Please upload a resume first" : "Optimize your resume details for this job posting"}
                        >
                          ✨ Tailor Resume
                        </button>

                        <button 
                          className="btn-secondary" 
                          onClick={() => handleDownloadPdf(job.id)}
                          disabled={!resume}
                          title={!resume ? "Please upload a resume first" : "Download tailored PDF resume"}
                        >
                          📄 Download PDF
                        </button>
                        
                        {job.job_url && (
                          <a 
                            href={job.job_url} 
                            target="_blank" 
                            rel="noopener noreferrer" 
                            className="btn-accent-link"
                          >
                            🔗 View Listing & Apply Manually
                          </a>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            </>
          )}
        </main>
      </div>

      {/* Tailoring Modal */}
      {showModal && selectedJob && (
        <div className="modal-overlay">
          <div className="modal-content">
            <header className="modal-header">
              <div className="modal-title-area">
                <h2>Tailoring Resume</h2>
                <p>{selectedJob.title} — {selectedJob.company}</p>
              </div>
              <button className="btn-close" onClick={() => setShowModal(false)}>✕</button>
            </header>

            <div className="modal-body">
              {tailorLoading ? (
                <div style={{ gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '300px' }}>
                  <div className="spinner" />
                  <p className="loader-text">AI is tailoring summary, experience & skills lists to target this posting...</p>
                </div>
              ) : (
                <>
                  {/* Left Column - Rationale & Changes list */}
                  <div className="modal-sidebar">
                    <div className="modal-sidebar-card">
                      <h4>Modifications Made</h4>
                      {tailoredResume && tailoredResume.changes && tailoredResume.changes.length > 0 ? (
                        <div className="changes-list">
                          {tailoredResume.changes.map((item, i) => (
                            <div key={i} className="change-item">
                              <div className="change-title">{item.section}</div>
                              <div className="change-desc">{item.change}</div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p style={{ fontSize: '0.8125rem', color: 'var(--color-text-muted)' }}>Resume tailored with custom vocabulary adjustments.</p>
                      )}
                    </div>
                    
                    <p style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', lineHeight: 1.4 }}>
                      ⚠️ <b>Notice:</b> The AI rephrases summary and experience bullet points to focus on requested skills. Review the text below to make sure it aligns with your true experience before copy/pasting.
                    </p>
                  </div>

                  {/* Right Column - Resume Text Markdown Preview */}
                  <div className="resume-preview-container">
                    <div className="resume-markdown-box">
                      {tailoredResume ? tailoredResume.markdown : 'No resume tailored.'}
                    </div>
                  </div>
                </>
              )}
            </div>

            <footer className="modal-footer">
              {copySuccess && (
                <div className="copy-badge">
                  ✓ Copied to clipboard!
                </div>
              )}
              <button className="btn-secondary" onClick={() => setShowModal(false)}>
                Close
              </button>
              <button 
                className="btn-secondary" 
                onClick={() => handleDownloadPdf(selectedJob.id)}
                disabled={tailorLoading || !tailoredResume}
                style={{ width: 'auto', padding: '0.75rem 1.5rem' }}
              >
                📥 Download PDF
              </button>
              <button 
                className="btn-primary" 
                onClick={copyToClipboard}
                disabled={tailorLoading || !tailoredResume}
                style={{ width: 'auto', padding: '0.75rem 1.5rem' }}
              >
                📋 Copy Optimized Markdown
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
