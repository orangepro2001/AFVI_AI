<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\View\View;
use JsonException;
use Symfony\Component\HttpFoundation\BinaryFileResponse;
use Symfony\Component\HttpKernel\Exception\NotFoundHttpException;

class ReviewController extends Controller
{
    private const PAGE_SIZE = 200;

    private const MAX_PAGE = 2000;

    public function index(): View
    {
        $summary = $this->summary();
        $lots = [];
        foreach ($summary['lots'] ?? [] as $entry) {
            if (! is_array($entry) || ! is_string($entry['lot'] ?? null)) {
                continue;
            }
            $families = array_values(array_filter((array) ($entry['families'] ?? []), 'is_string'));
            $lots[] = [
                'lot' => $entry['lot'],
                'families' => $families,
                'first_inspected_at' => is_string($entry['first_inspected_at'] ?? null) ? substr($entry['first_inspected_at'], 0, 10) : null,
                'scored_points' => $this->count($entry['scored_points'] ?? null),
                'vrs_keyed_points' => $this->count($entry['vrs_keyed_points'] ?? null),
                'in_training' => (bool) ($entry['lot_in_model_training'] ?? false),
                'trust' => $this->lotTrust($families, $summary),
            ];
        }
        usort($lots, fn (array $a, array $b) => strcmp((string) $b['first_inspected_at'], (string) $a['first_inspected_at']));

        return view('review.index', [
            'summary' => $summary,
            'lots' => $lots,
            'model' => is_array($summary['model'] ?? null) ? $summary['model'] : [],
        ]);
    }

    public function show(Request $request, string $lot): View
    {
        $summary = $this->summary();
        $entry = $this->lotEntry($summary, $lot);
        $page = min(self::MAX_PAGE, max(1, (int) $request->query('page', 1)));
        $side = in_array($request->query('side'), ['FC', 'BC'], true) ? $request->query('side') : null;
        $hideDummy = $request->boolean('hide_dummy');
        $start = ($page - 1) * self::PAGE_SIZE;

        $rows = [];
        $matched = 0;
        $handle = fopen($this->lotFile($entry), 'r');
        $header = fgetcsv($handle, escape: '');
        if (is_array($header) && isset($header[0])) {
            $header[0] = preg_replace('/^\xEF\xBB\xBF/', '', $header[0]);
        }
        while (($line = fgetcsv($handle, escape: '')) !== false) {
            if (! is_array($header) || count($line) !== count($header)) {
                continue;
            }
            $row = array_combine($header, $line);
            if ($side !== null && $row['side'] !== $side) {
                continue;
            }
            if ($hideDummy && $row['is_dummy'] === 'True') {
                continue;
            }
            if ($matched++ < $start) {
                continue;
            }
            $rows[] = $row;
            if (count($rows) > self::PAGE_SIZE) {
                break;
            }
        }
        fclose($handle);
        $hasNext = count($rows) > self::PAGE_SIZE;
        $rows = array_slice($rows, 0, self::PAGE_SIZE);
        $families = array_values(array_filter((array) ($entry['families'] ?? []), 'is_string'));

        return view('review.show', [
            'lot' => $entry['lot'],
            'entry' => $entry,
            'rows' => $rows,
            'page' => $page,
            'hasNext' => $hasNext,
            'side' => $side,
            'hideDummy' => $hideDummy,
            'trust' => $this->lotTrust($families, $summary),
            'familyEvidence' => is_array($summary['family_evidence'] ?? null) ? $summary['family_evidence'] : [],
            'model' => is_array($summary['model'] ?? null) ? $summary['model'] : [],
        ]);
    }

    public function download(string $lot): BinaryFileResponse
    {
        $entry = $this->lotEntry($this->summary(), $lot);

        return response()->download($this->lotFile($entry), $entry['lot'].'-review.csv', ['Content-Type' => 'text/csv; charset=utf-8']);
    }

    private function directory(): string
    {
        $run = basename((string) config('afvi.review_run', 'current'));

        return rtrim((string) config('afvi.data_home'), DIRECTORY_SEPARATOR).'/reports/review/'.$run;
    }

    /**
     * @return array<string, mixed>
     */
    private function summary(): array
    {
        $path = $this->directory().'/summary.json';
        if (! is_file($path) || ! is_readable($path)) {
            return [];
        }
        try {
            $decoded = json_decode((string) file_get_contents($path), true, 512, JSON_THROW_ON_ERROR);
        } catch (JsonException) {
            return [];
        }

        return is_array($decoded) ? $decoded : [];
    }

    /**
     * @param  array<string, mixed>  $summary
     * @return array<string, mixed>
     */
    private function lotEntry(array $summary, string $lot): array
    {
        foreach ($summary['lots'] ?? [] as $entry) {
            if (is_array($entry) && ($entry['lot'] ?? null) === $lot) {
                return $entry;
            }
        }

        throw new NotFoundHttpException;
    }

    /**
     * @param  array<string, mixed>  $entry
     */
    private function lotFile(array $entry): string
    {
        $directory = realpath($this->directory());
        $path = realpath($this->directory().'/'.basename((string) ($entry['file'] ?? '')));
        if ($directory === false || $path === false || ! str_starts_with($path, $directory.DIRECTORY_SEPARATOR) || ! is_file($path)) {
            throw new NotFoundHttpException;
        }

        return $path;
    }

    /**
     * @param  list<string>  $families
     * @param  array<string, mixed>  $summary
     * @return array{level: string, label: string, detail: string}
     */
    private function lotTrust(array $families, array $summary): array
    {
        $evidence = is_array($summary['family_evidence'] ?? null) ? $summary['family_evidence'] : [];
        $worst = null;
        foreach ($families as $family) {
            $auc = $evidence[$family]['heldout_roc_auc'] ?? null;
            if (! is_numeric($auc)) {
                return ['level' => 'unverified', 'label' => '未验证', 'detail' => $family.' 没有留出 LOT 证据'];
            }
            $worst = $worst === null ? (float) $auc : min($worst, (float) $auc);
        }
        if ($worst === null) {
            return ['level' => 'unverified', 'label' => '未验证', 'detail' => '没有型号信息'];
        }
        $detail = '留出 LOT ROC AUC '.number_format($worst, 3);

        return match (true) {
            $worst >= 0.75 => ['level' => 'strong', 'label' => '强', 'detail' => $detail],
            $worst >= 0.65 => ['level' => 'moderate', 'label' => '中', 'detail' => $detail],
            default => ['level' => 'weak', 'label' => '弱', 'detail' => $detail],
        };
    }

    private function count(mixed $value): int
    {
        return is_numeric($value) && (int) $value >= 0 ? (int) $value : 0;
    }
}
