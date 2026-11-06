<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\View\View;
use JsonException;
use Symfony\Component\HttpKernel\Exception\NotFoundHttpException;

class BaselineController extends Controller
{
    private const PAGE_SIZE = 200;

    public function index(Request $request): View
    {
        $index = $this->read('index.json') ?? [];
        [$vision, $light] = $this->selection($request, $index);
        $file = $vision ? $this->read($vision['name'].'-L'.$light.'.json') : null;
        $rows = is_array($file['rows'] ?? null) ? $file['rows'] : [];

        $query = trim((string) $request->query('q', ''));
        $view = in_array($request->query('view'), ['all', 'strong', 'split'], true) ? $request->query('view') : 'all';
        $tag = in_array($request->query('tag'), ['INSPECTION', 'MASTER', 'SUBMASTER'], true) ? $request->query('tag') : null;
        $share = (float) ($index['consensus_share'] ?? 0.6);
        $minModels = (int) ($index['min_consensus_models'] ?? 5);
        $filtered = array_values(array_filter($rows, function (array $row) use ($query, $view, $tag, $share, $minModels): bool {
            if (($row['active'] ?? 0) === 0) {
                return false;
            }
            if ($tag !== null && ($row['tag'] ?? null) !== $tag) {
                return false;
            }
            $mode = $row['stats']['mode_share'] ?? 0;
            if ($view === 'strong' && ! (($row['active'] ?? 0) >= $minModels && $mode >= $share)) {
                return false;
            }
            if ($view === 'split' && ! (($row['active'] ?? 0) >= $minModels && $mode < $share)) {
                return false;
            }
            if ($query === '') {
                return true;
            }
            $haystack = implode(' ', [$row['key'] ?? '', $row['name_en'] ?? '', $row['name_ko'] ?? '', $row['node'] ?? '', $row['node_name'] ?? '']);

            return mb_stripos($haystack, $query) !== false;
        }));
        $page = max(1, (int) $request->query('page', 1));
        $total = count($filtered);

        return view('baseline.index', [
            'index' => $index,
            'vision' => $vision,
            'light' => $light,
            'rows' => array_slice($filtered, ($page - 1) * self::PAGE_SIZE, self::PAGE_SIZE),
            'total' => $total,
            'page' => $page,
            'hasNext' => $page * self::PAGE_SIZE < $total,
            'query' => $query,
            'viewMode' => $view,
            'tag' => $tag,
            'models' => $vision ? ($this->read($vision['name'].'-models.json')['models'] ?? []) : [],
        ]);
    }

    public function model(Request $request): View
    {
        $index = $this->read('index.json') ?? [];
        [$vision, $light] = $this->selection($request, $index);
        if ($vision === null) {
            throw new NotFoundHttpException;
        }
        $models = $this->read($vision['name'].'-models.json')['models'] ?? [];
        $model = (string) $request->query('model', '');
        if (! in_array($model, $models, true)) {
            throw new NotFoundHttpException;
        }
        $own = $this->read($vision['name'].'-model/'.$model.'.json');
        $values = is_array($own['lights'][$light] ?? null) ? $own['lights'][$light] : [];
        $rows = $this->read($vision['name'].'-L'.$light.'.json')['rows'] ?? [];
        $share = (float) ($index['consensus_share'] ?? 0.6);
        $minModels = (int) ($index['min_consensus_models'] ?? 5);
        $onlyDifferences = $request->query('all') !== '1';

        $compared = [];
        $differences = 0;
        foreach ($rows as $row) {
            $id = ($row['node'] ?? '').'|'.($row['tag'] ?? '').'|'.($row['key'] ?? '').'|'.($row['channel'] ?? '');
            if (! array_key_exists($id, $values)) {
                continue;
            }
            [$value, $enabled] = $values[$id] + [null, false];
            $stats = $row['stats'] ?? null;
            $strong = is_array($stats) && ($row['active'] ?? 0) >= $minModels && ($stats['mode_share'] ?? 0) >= $share;
            $active = $enabled && $value !== null && ! (($row['tag'] ?? '') === 'INSPECTION' && (float) $value === 0.0);
            $differs = $strong && $active && (float) $value !== (float) $stats['mode'];
            $differences += $differs ? 1 : 0;
            if ($onlyDifferences && ! $differs) {
                continue;
            }
            $compared[] = ['row' => $row, 'value' => $value, 'enabled' => $enabled, 'active' => $active, 'differs' => $differs, 'strong' => $strong];
        }

        $family = preg_replace('/-\d{2}$/', '', $model);
        $evidence = [];
        $sideCode = ($vision['side'] ?? '') === 'BOTTOM' ? 'BC' : 'FC';
        foreach ($this->read('ai-evidence.json') ?? [] as $name => $entries) {
            if (strcasecmp((string) $name, (string) $family) !== 0 || ! is_array($entries)) {
                continue;
            }
            foreach ($entries as $entry) {
                if (is_array($entry) && ($entry['side'] ?? null) === $sideCode && (string) ($entry['light'] ?? '') === (string) $light) {
                    $evidence[] = $entry;
                }
            }
        }

        return view('baseline.model', [
            'index' => $index,
            'vision' => $vision,
            'light' => $light,
            'model' => $model,
            'family' => $family,
            'rows' => $compared,
            'differences' => $differences,
            'onlyDifferences' => $onlyDifferences,
            'evidence' => $evidence,
            'sideCode' => $sideCode,
            'paraLink' => rtrim((string) ($index['para_url'] ?? '/afvi/para/'), '/').'/#'.rawurlencode($vision['name']).'/'.rawurlencode($model).'/'.rawurlencode((string) $light),
        ]);
    }

    /**
     * @param  array<string, mixed>  $index
     * @return array{0: array<string, mixed>|null, 1: string}
     */
    private function selection(Request $request, array $index): array
    {
        $visions = array_values(array_filter((array) ($index['visions'] ?? []), 'is_array'));
        $wanted = (string) $request->query('vision', $visions[0]['name'] ?? '');
        $vision = null;
        foreach ($visions as $candidate) {
            if (($candidate['name'] ?? null) === $wanted) {
                $vision = $candidate;
            }
        }
        $vision ??= $visions[0] ?? null;
        $lights = array_map('strval', (array) ($vision['lights'] ?? []));
        $light = (string) $request->query('light', $lights[0] ?? '0');
        if (! in_array($light, $lights, true)) {
            $light = $lights[0] ?? '0';
        }

        return [$vision, $light];
    }

    /**
     * @return array<string, mixed>|null
     */
    private function read(string $relative): ?array
    {
        if (str_contains($relative, '..')) {
            return null;
        }
        $path = rtrim((string) config('afvi.data_home'), DIRECTORY_SEPARATOR).'/reports/baseline/'.$relative;
        if (! is_file($path) || ! is_readable($path)) {
            return null;
        }
        try {
            $decoded = json_decode((string) file_get_contents($path), true, 512, JSON_THROW_ON_ERROR);
        } catch (JsonException) {
            return null;
        }

        return is_array($decoded) ? $decoded : null;
    }
}
