"use client";

import { useEffect, useState } from "react";
import Link from "next/link";


export default function DownloadPage() {
  const [releases, setReleases] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("https://api.github.com/repos/GamerNation12/DJ-Scratch/releases")
      .then(res => {
        if (!res.ok) throw new Error("Failed to fetch releases");
        return res.json();
      })
      .then(data => {
        setReleases(data);
        setLoading(false);
      })
      .catch(err => {
        console.error(err);
        setError("Could not load releases at this time.");
        setLoading(false);
      });
  }, []);

  return (
    <div className="min-h-screen bg-[#09090b] text-white font-sans selection:bg-indigo-500/30 overflow-hidden relative flex flex-col items-center">
      {/* Background Effects */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none z-0 flex justify-center items-center opacity-30">
        <div className="absolute w-[800px] h-[800px] bg-indigo-600/20 rounded-full blur-[120px] mix-blend-screen animate-blob"></div>
        <div className="absolute w-[600px] h-[600px] bg-emerald-500/10 rounded-full blur-[120px] mix-blend-screen animate-blob animation-delay-200"></div>
      </div>
      <div className="absolute inset-0 bg-[url('/noise.svg')] opacity-[0.03] pointer-events-none z-0 mix-blend-overlay"></div>
      
      <main className="relative z-10 w-full max-w-5xl mx-auto px-4 pt-24 pb-24">
        <Link href="/" className="inline-flex items-center gap-2 text-zinc-400 hover:text-white transition-colors mb-12 font-medium">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg> Back to Home
        </Link>
        
        <div className="text-center mb-16">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-3xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 mb-6 shadow-[0_0_30px_rgba(99,102,241,0.15)]">
            <svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          </div>
          <h1 className="text-4xl md:text-6xl font-black tracking-tighter mb-4 text-transparent bg-clip-text bg-gradient-to-r from-white to-zinc-400">
            Download DJ Scratch
          </h1>
          <p className="text-xl text-zinc-400 max-w-2xl mx-auto font-medium">
            Get the ultimate Last.fm and Discord experience right on your desktop or mobile device.
          </p>
        </div>

        {loading ? (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="w-12 h-12 border-4 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin mb-4"></div>
            <div className="text-zinc-500 font-medium animate-pulse">Loading latest releases...</div>
          </div>
        ) : error ? (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-8 rounded-3xl text-center max-w-2xl mx-auto">
            <h3 className="text-xl font-bold mb-2">Oops!</h3>
            <p>{error}</p>
            <p className="mt-4 text-sm opacity-80">Please check back later or visit our GitHub page directly.</p>
          </div>
        ) : releases.length === 0 ? (
          <div className="bg-zinc-900/50 backdrop-blur-xl border border-white/10 p-12 rounded-3xl text-center max-w-2xl mx-auto">
            <h3 className="text-2xl font-bold mb-3">No releases yet!</h3>
            <p className="text-zinc-400">We are currently preparing the first public release. Please check back soon.</p>
          </div>
        ) : (
          <div className="space-y-8">
            {releases.map((release, idx) => {
              const exeAsset = release.assets.find((a: any) => a.name.endsWith('.exe'));
              const apkAsset = release.assets.find((a: any) => a.name.endsWith('.apk'));
              const linuxAsset = release.assets.find((a: any) => a.name.endsWith('.AppImage')) || release.assets.find((a: any) => a.name.endsWith('.deb'));
              const isLatest = idx === 0;

              return (
                <div key={release.id} className={`bg-zinc-900/40 backdrop-blur-md border ${isLatest ? 'border-indigo-500/30 shadow-[0_0_40px_rgba(99,102,241,0.1)]' : 'border-white/10'} rounded-3xl p-6 md:p-8 overflow-hidden relative group`}>
                  {isLatest && (
                    <div className="absolute top-0 right-0 w-64 h-64 bg-indigo-500/10 rounded-full blur-[80px] pointer-events-none -mr-32 -mt-32"></div>
                  )}
                  
                  <div className="flex flex-col md:flex-row gap-8 relative z-10">
                    <div className="flex-1">
                      <div className="flex items-center gap-3 mb-4">
                        <h2 className="text-2xl md:text-3xl font-bold tracking-tight text-white">{release.name || release.tag_name}</h2>
                        {isLatest && (
                          <span className="px-3 py-1 bg-indigo-500/20 text-indigo-300 text-xs font-black uppercase tracking-widest rounded-full border border-indigo-500/30">
                            Latest
                          </span>
                        )}
                        {release.prerelease && (
                          <span className="px-3 py-1 bg-amber-500/20 text-amber-300 text-xs font-black uppercase tracking-widest rounded-full border border-amber-500/30">
                            Beta
                          </span>
                        )}
                      </div>
                      
                      <div className="text-sm text-zinc-500 font-mono mb-6 flex items-center gap-2">
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4"/><path d="M9 18c-4.51 2-5-2-7-2"/></svg>
                        Published on {new Date(release.published_at).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}
                      </div>

                      <div className="prose prose-invert prose-zinc max-w-none prose-p:text-zinc-400 prose-a:text-indigo-400 hover:prose-a:text-indigo-300">
                        {/* We use a simple whitespace pre-wrap for the markdown body to render it cleanly without a heavy markdown parser for now */}
                        <div className="whitespace-pre-wrap text-zinc-300 font-medium leading-relaxed bg-zinc-950/50 p-6 rounded-2xl border border-white/5">
                          {release.body || "No release notes provided."}
                        </div>
                      </div>
                    </div>

                    <div className="md:w-72 flex flex-col gap-4 shrink-0 border-t md:border-t-0 md:border-l border-white/10 pt-6 md:pt-0 md:pl-8">
                      <h3 className="text-sm font-bold text-zinc-500 uppercase tracking-widest mb-2">Assets</h3>
                      
                      {exeAsset ? (
                        <div className="flex flex-col gap-2">
                          <a 
                            href={exeAsset.browser_download_url}
                            className="flex items-center gap-4 bg-indigo-500 hover:bg-indigo-400 transition-colors p-4 rounded-2xl group/btn"
                          >
                            <div className="w-10 h-10 bg-white/20 rounded-xl flex items-center justify-center shrink-0">
                              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white"><rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/></svg>
                            </div>
                            <div>
                              <div className="font-bold text-white leading-tight">Windows</div>
                              <div className="text-xs text-indigo-100 mt-1">.exe • {(exeAsset.size / 1024 / 1024).toFixed(1)} MB</div>
                            </div>
                          </a>
                          <div className="text-[10px] text-zinc-500 bg-black/20 p-3 rounded-xl border border-white/5 leading-relaxed">
                            <span className="font-bold text-zinc-400">Note:</span> If Microsoft Defender SmartScreen pops up, click <b>More info</b> &rarr; <b>Run anyway</b>. This happens for all unsigned indie apps!
                          </div>
                        </div>
                      ) : (
                        <div className="flex items-center gap-4 bg-zinc-800/50 p-4 rounded-2xl opacity-50 cursor-not-allowed border border-white/5">
                          <div className="w-10 h-10 bg-zinc-700 rounded-xl flex items-center justify-center shrink-0">
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-zinc-500"><rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/></svg>
                          </div>
                          <div>
                            <div className="font-bold text-zinc-400 leading-tight">Windows</div>
                            <div className="text-xs text-zinc-500 mt-1">Not available</div>
                          </div>
                        </div>
                      )}

                      {apkAsset ? (
                        <a 
                          href={apkAsset.browser_download_url}
                          className="flex items-center gap-4 bg-emerald-500 hover:bg-emerald-400 transition-colors p-4 rounded-2xl group/btn"
                        >
                          <div className="w-10 h-10 bg-white/20 rounded-xl flex items-center justify-center shrink-0">
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white"><rect width="14" height="20" x="5" y="2" rx="2" ry="2"/><path d="M12 18h.01"/></svg>
                          </div>
                          <div>
                            <div className="font-bold text-white leading-tight">Android</div>
                            <div className="text-xs text-emerald-100 mt-1">.apk • {(apkAsset.size / 1024 / 1024).toFixed(1)} MB</div>
                          </div>
                        </a>
                      ) : (
                        <div className="flex items-center gap-4 bg-zinc-800/50 p-4 rounded-2xl opacity-50 cursor-not-allowed border border-white/5">
                          <div className="w-10 h-10 bg-zinc-700 rounded-xl flex items-center justify-center shrink-0">
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-zinc-500"><rect width="14" height="20" x="5" y="2" rx="2" ry="2"/><path d="M12 18h.01"/></svg>
                          </div>
                          <div>
                            <div className="font-bold text-zinc-400 leading-tight">Android</div>
                            <div className="text-xs text-zinc-500 mt-1">Not available</div>
                          </div>
                        </div>
                      )}

                      {linuxAsset ? (
                        <a
                          href={linuxAsset.browser_download_url}
                          className="flex items-center gap-4 bg-amber-500 hover:bg-amber-400 transition-colors p-4 rounded-2xl group/btn"
                        >
                          <div className="w-10 h-10 bg-white/20 rounded-xl flex items-center justify-center shrink-0">
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white"><rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/></svg>
                          </div>
                          <div>
                            <div className="font-bold text-white leading-tight">Linux</div>
                            <div className="text-xs text-amber-100 mt-1">{linuxAsset.name.endsWith('.deb') ? '.deb' : '.AppImage'} • {(linuxAsset.size / 1024 / 1024).toFixed(1)} MB</div>
                          </div>
                        </a>
                      ) : (
                        <div className="flex items-center gap-4 bg-zinc-800/50 p-4 rounded-2xl opacity-50 cursor-not-allowed border border-white/5">
                          <div className="w-10 h-10 bg-zinc-700 rounded-xl flex items-center justify-center shrink-0">
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-zinc-500"><rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/></svg>
                          </div>
                          <div>
                            <div className="font-bold text-zinc-400 leading-tight">Linux</div>
                            <div className="text-xs text-zinc-500 mt-1">Not available</div>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
