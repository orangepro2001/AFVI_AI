<?php

use App\Http\Controllers\DashboardController;
use App\Http\Controllers\ReviewController;
use App\Http\Controllers\VisionController;
use Illuminate\Support\Facades\Route;

$pages = function (string $namePrefix): void {
    Route::get('/', DashboardController::class)->name($namePrefix.'dashboard');
    Route::get('/review', [ReviewController::class, 'index'])->name($namePrefix.'review.index');
    Route::get('/review/{lot}', [ReviewController::class, 'show'])
        ->where('lot', '[A-Za-z0-9][A-Za-z0-9._-]*')
        ->name($namePrefix.'review.show');
    Route::get('/review/{lot}/csv', [ReviewController::class, 'download'])
        ->where('lot', '[A-Za-z0-9][A-Za-z0-9._-]*')
        ->name($namePrefix.'review.download');
    Route::get('/vision', [VisionController::class, 'show'])->name($namePrefix.'vision.show');
    Route::post('/vision', [VisionController::class, 'predict'])->name($namePrefix.'vision.predict');
};

$pages('');

$prefix = trim((string) config('afvi.public_prefix', '/afvi_ai'), '/');
if ($prefix !== '') {
    Route::prefix($prefix)->group(fn () => $pages('prefixed.'));
}
