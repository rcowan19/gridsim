// Stands in for supabase-js in UI tests. The test injects window.__TOKEN (a real HS256 token the test server accepts).
window.supabase = {createClient: () => {
  const load = () => { try { return JSON.parse(localStorage.getItem('fake_session') || 'null'); } catch (e) { return null; } };
  const set = (s) => { if (s) localStorage.setItem('fake_session', JSON.stringify(s)); else localStorage.removeItem('fake_session'); };
  return {auth: {
    getSession: async () => ({data: {session: load()}}),
    signInWithPassword: async ({email, password}) => { if (password === 'wrong-password') return {data: {}, error: {message: 'Invalid login credentials'}}; const s = {access_token: window.__TOKEN, user: {email}}; set(s); return {data: {session: s}, error: null}; },
    signUp: async ({email}) => ({data: {session: null}, error: null}),
    signInWithOAuth: async () => ({error: null}),
    resetPasswordForEmail: async () => ({error: null}),
    updateUser: async () => ({error: null}),
    signOut: async () => { set(null); return {error: null}; },
    onAuthStateChange: () => ({data: {subscription: {unsubscribe() {}}}}),
  }};
}};
