<script>
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { setSubnav } from '$lib/app.svelte.js';
  import { stories, loadStories, loadModels } from '$lib/stories.svelte.js';
  import { charName, loadChars } from '$lib/characters.svelte.js';

  let { children } = $props();

  const TOP = [{ id: 'library', label: 'Library' }, { id: 'config', label: '⚙ Config' }];
  const SECTIONS = [
    { id: 'overview', label: 'Overview' },
    { id: 'backgrounds', label: 'Backgrounds' },
    { id: 'cast', label: 'Cast & wardrobe' },
    { id: 'edit', label: '✎ Edit story' },
    { id: 'play', label: '▶ Play' }
  ];

  // Parse the route: [] = library · ['config'] · ['new', step?] = wizard · ['<key>', section?] = open story.
  // The story-GENERATION wizard navigates via an in-page header strip (see stories/new/+layout.svelte);
  // the open story's EDITING sections stay in this left panel as the sub-tier.
  let segs = $derived(page.url.pathname.replace(/^\/stories\/?/, '').split('/').filter(Boolean));
  let inStory = $derived(segs.length >= 1 && segs[0] !== 'new' && segs[0] !== 'config');
  let storyKey = $derived(inStory ? segs[0] : null);
  let section = $derived(inStory ? (segs[1] || 'overview') : null);

  // Cast & wardrobe breaks down into one entry per cast member (third nav tier). Selecting one
  // opens just that character via ?c=<key>; default to the primary / first member.
  let castMembers = $derived(
    (inStory && stories.current?.key === storyKey ? stories.current.cast : [])
      .map((m) => ({ id: m.character, label: charName(m.character), primary: m.primary })));
  // Only highlight a cast member when one is actually open (?c=…). On the dashboard (no ?c=),
  // nothing under Cast & wardrobe is selected — don't auto-highlight the primary.
  let castChild = $derived(page.url.searchParams.get('c') || '');

  // Panel: Library · Config, with the open story's editing sections as the sub-tier. (The wizard
  // shows nothing extra here — it navigates through its own in-page header.)
  $effect(() => {
    let sub = null;
    if (inStory) {
      const items = SECTIONS.map((s) =>
        s.id === 'cast' && castMembers.length ? { ...s, children: castMembers } : s);
      sub = {
        items, value: section,
        // 'Cast & wardrobe' opens the dashboard (no ?c=); a cast-member child opens that member.
        onpick: (id) => goto(id === 'cast' ? `/stories/${storyKey}/cast` : `/stories/${storyKey}/${id}`),
        childValue: castChild,
        onpickChild: (cid) => goto(`/stories/${storyKey}/cast?c=${cid}`),
      };
    }
    setSubnav({
      items: TOP,
      value: segs[0] === 'config' ? 'config' : 'library',
      onpick: (id) => goto(id === 'config' ? '/stories/config' : '/stories'),
      sub
    });
  });

  onMount(() => { loadStories(); loadChars(); loadModels(); });
</script>

{@render children()}
